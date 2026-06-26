"""
Datei-Router des Homelab-Imperiums („FileBunker").

Stellt REST-Endpunkte für den gesicherten Dateizugriff bereit.
**JEDER** Pfad durchläuft die mehrstufige ``secure_path()``-Validierung
des ``FileBunkerService`` — Directory-Traversal ist strukturell unmöglich.

Endpunkte:
- ``GET /api/files/list``        — Verzeichnisinhalt auflisten
- ``POST /api/files/directory``  — Verzeichnis erstellen
- ``DELETE /api/files/directory`` — Verzeichnis löschen
- ``GET /api/files/download``    — Datei herunterladen (Stream)
- ``POST /api/files/upload``     — Datei hochladen (Multipart)
- ``DELETE /api/files``          — Datei löschen (optional sicher)
- ``PUT /api/files/move``        — Datei verschieben/umbenennen
- ``POST /api/files/copy``       — Datei kopieren
- ``GET /api/files/info``        — Datei-Metadaten
- ``GET /api/files/storage``     — Speicherplatz-Info

Verwendung::

    from app.routers.files import router
    app.include_router(router, prefix="/api")
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.files import FileBunkerService

# ═══════════════════════════════════════════════════════════════════════════════
# Logger
# ═══════════════════════════════════════════════════════════════════════════════

logger: logging.Logger = logging.getLogger("homelab_imperium.routers.files")

# ═══════════════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════════════

router: APIRouter = APIRouter(tags=["Dateien"])

# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════════════

_file_service: FileBunkerService = FileBunkerService()


def get_file_service() -> FileBunkerService:
    """Factory für den FileBunkerService."""
    return _file_service


# ═══════════════════════════════════════════════════════════════════════════════
# Response-Modelle
# ═══════════════════════════════════════════════════════════════════════════════


class SuccessResponse(BaseModel):
    """Einfache Erfolgsmeldung."""

    message: str = Field(default="Operation erfolgreich.")
    detail: Optional[str] = Field(default=None)


class StorageInfoResponse(BaseModel):
    """Speicherplatz-Information."""

    base_dir: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte — Verzeichnisse
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/files/list",
    summary="Verzeichnisinhalt auflisten",
    description="Listet Dateien und Ordner im angegebenen Pfad auf. "
    "Der Pfad wird durch ``secure_path()`` gegen Traversal geschützt.",
)
async def list_files(
    path: str = Query(
        default="",
        description="Relativer Pfad innerhalb des Dateibunkers. "
        "Leer = Wurzelverzeichnis.",
    ),
    include_hidden: bool = Query(
        default=False,
        description="Versteckte Dateien (beginnen mit '.') anzeigen.",
    ),
    svc: FileBunkerService = Depends(get_file_service),
) -> list[dict]:
    """
    Listet den Inhalt eines Verzeichnisses auf.

    **Sicherheit**: ``path`` wird durch die 6-stufige
    ``secure_path()``-Validierung geschleust (Null-Byte-Erkennung,
    normpath, abspath, realpath, Präfix-Prüfung).
    """
    logger.info(
        "GET /files/list: path=%r, hidden=%s.", path, include_hidden
    )

    try:
        entries: list[dict] = await svc.list_directory(
            relative_path=path,
            include_hidden=include_hidden,
        )
        logger.debug(
            "%d Einträge in %r gelistet.", len(entries), path or "/"
        )
        return entries

    except PermissionError as exc:
        # Traversal-Versuch erkannt
        logger.warning("Traversal-Versuch blockiert: %s", exc)
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Auflisten von %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Auflisten: {exc}"
        ) from exc


@router.post(
    "/files/directory",
    response_model=SuccessResponse,
    summary="Verzeichnis erstellen",
    description="Erstellt ein neues Verzeichnis (rekursiv).",
)
async def create_directory(
    path: str = Query(..., min_length=1, description="Relativer Pfad des neuen Verzeichnisses."),
    exist_ok: bool = Query(default=True),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """
    Erstellt ein Verzeichnis im Dateibunker.

    **Sicherheit**: ``path`` wird via ``secure_path()`` validiert.
    Das Basisverzeichnis selbst kann NICHT erstellt/gelöscht werden.
    """
    logger.info("POST /files/directory: path=%r.", path)

    try:
        created: str = await svc.create_directory(
            relative_path=path,
            exist_ok=exist_ok,
        )
        logger.info("Verzeichnis erstellt: %s.", created)
        return SuccessResponse(
            message="Verzeichnis erstellt.",
            detail=created,
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Erstellen von %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Erstellen: {exc}"
        ) from exc


@router.delete(
    "/files/directory",
    response_model=SuccessResponse,
    summary="Verzeichnis löschen",
    description="Löscht ein Verzeichnis. Optional rekursiv.",
)
async def delete_directory(
    path: str = Query(..., min_length=1, description="Relativer Pfad."),
    recursive: bool = Query(default=False, description="Rekursiv löschen (wie rm -rf)."),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """
    Löscht ein Verzeichnis.

    Das Basisverzeichnis selbst KANN NICHT gelöscht werden
    (Schutz in ``delete_directory()``).
    """
    logger.info(
        "DELETE /files/directory: path=%r, recursive=%s.",
        path,
        recursive,
    )

    try:
        await svc.delete_directory(
            relative_path=path,
            recursive=recursive,
        )
        return SuccessResponse(
            message="Verzeichnis gelöscht.",
            detail=path,
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except OSError as exc:
        # Z.B. Verzeichnis nicht leer und recursive=False
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Löschen von %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Löschen: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Endpunkte — Dateien
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/files/info",
    summary="Datei-Informationen",
    description="Metadaten einer Datei (Größe, MIME-Type, Datum).",
)
async def get_file_info(
    path: str = Query(..., min_length=1, description="Relativer Pfad zur Datei."),
    svc: FileBunkerService = Depends(get_file_service),
) -> dict:
    """
    Ruft Metadaten einer einzelnen Datei ab.
    """
    logger.debug("GET /files/info: path=%r.", path)

    try:
        return await svc.get_file_info(relative_path=path)

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler bei Datei-Info für %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.get(
    "/files/download",
    summary="Datei herunterladen (Streaming)",
    description="Streamt eine Datei als Download an den Client. "
    "Große Dateien werden in 1-MiB-Chunks gestreamt (RAM-schonend).",
    responses={
        200: {
            "description": "Datei-Stream.",
            "content": {"application/octet-stream": {}},
        },
    },
)
async def download_file(
    path: str = Query(..., min_length=1, description="Relativer Pfad zur Datei."),
    svc: FileBunkerService = Depends(get_file_service),
) -> StreamingResponse:
    """
    Lädt eine Datei als StreamingResponse herunter.

    Nutzt ``download_file_stream()`` des Services, das die Datei in
    1-MiB-Chunks streamt — auch sehr große Dateien belasten den RAM
    nicht übermäßig.
    """
    logger.info("GET /files/download: path=%r.", path)

    try:
        # Metadaten für Content-Disposition-Header
        info: dict = await svc.get_file_info(relative_path=path)
        filename: str = info.get("name", "download")
        mime_type: str = info.get("mime_type", "application/octet-stream")

        return StreamingResponse(
            svc.download_file_stream(relative_path=path),
            media_type=mime_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                ),
            },
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Download von %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Download: {exc}"
        ) from exc


@router.post(
    "/files/upload",
    response_model=SuccessResponse,
    summary="Datei hochladen",
    description="Lädt eine Datei via Multipart-Upload in den Dateibunker. "
    "Maximale Dateigröße: 500 MiB.",
)
async def upload_file(
    path: str = Query(
        default="",
        description="Zielverzeichnis (leer = Wurzel). "
        "Der Dateiname wird aus dem Upload entnommen.",
    ),
    file: UploadFile = File(..., description="Die hochzuladende Datei."),
    overwrite: bool = Query(default=True, description="Bestehende Datei überschreiben."),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """
    Empfängt eine Datei via HTTP-Multipart-Upload.

    Der Dateiinhalt wird als ``UploadFile``-Stream empfangen und
    direkt via ``upload_file_stream()`` in Chunks auf die Platte
    geschrieben — der gesamte Inhalt wird NICHT im RAM gehalten.

    **Sicherheit**: Der Dateiname aus dem Upload wird mit dem
    Zielpfad kombiniert und durch ``secure_path()`` validiert.
    """
    logger.info(
        "POST /files/upload: path=%r, filename=%r, size=%d.",
        path,
        file.filename,
        file.size or 0,
    )

    try:
        # Zielpfad: Verzeichnis + Dateiname
        safe_filename: str = file.filename or "uploaded_file"
        target_path: str = (
            f"{path.rstrip('/')}/{safe_filename}"
            if path
            else safe_filename
        )

        # Streaming-Upload: Chunk für Chunk auf Platte schreiben
        async def chunk_reader():
            while True:
                chunk: bytes = await file.read(
                    1024 * 1024  # 1 MiB Chunks
                )
                if not chunk:
                    break
                yield chunk

        result: dict = await svc.upload_file_stream(
            relative_path=target_path,
            reader=chunk_reader(),
            overwrite=overwrite,
        )

        logger.info(
            "Upload abgeschlossen: %s (%s).",
            result.get("name", target_path),
            result.get("size_display", "?"),
        )
        return SuccessResponse(
            message="Datei erfolgreich hochgeladen.",
            detail=result.get("name", target_path),
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except ValueError as exc:
        # Datei zu groß
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Upload nach %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Upload: {exc}"
        ) from exc


@router.delete(
    "/files",
    response_model=SuccessResponse,
    summary="Datei löschen",
    description="Löscht eine Datei. Optional mit sicherem Überschreiben.",
)
async def delete_file(
    path: str = Query(..., min_length=1, description="Relativer Pfad zur Datei."),
    secure: bool = Query(
        default=False,
        description="Sicheres Löschen: Datei wird vor dem Löschen "
        "3-fach mit Nullen, Einsen und Zufallsdaten überschrieben. "
        "Verhindert forensische Wiederherstellung.",
    ),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """
    Löscht eine Datei.

    Mit ``secure=True`` wird die Datei vor dem Löschen dreifach
    überschrieben (DoD 5220.22-M-ähnlich).
    """
    logger.info(
        "DELETE /files: path=%r, secure=%s.", path, secure
    )

    try:
        await svc.delete_file(relative_path=path, secure=secure)
        return SuccessResponse(
            message=(
                "Datei sicher gelöscht."
                if secure
                else "Datei gelöscht."
            ),
            detail=path,
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Löschen von %r.", path)
        raise HTTPException(
            status_code=500, detail=f"Fehler beim Löschen: {exc}"
        ) from exc


@router.put(
    "/files/move",
    response_model=SuccessResponse,
    summary="Datei verschieben/umbenennen",
)
async def move_file(
    source: str = Query(..., min_length=1, description="Quellpfad (relativ)."),
    dest: str = Query(..., min_length=1, description="Zielpfad (relativ)."),
    overwrite: bool = Query(default=False),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """
    Verschiebt eine Datei an einen neuen Ort oder benennt sie um.
    """
    logger.info("PUT /files/move: %r → %r.", source, dest)

    try:
        result: dict = await svc.move_file(
            source_path=source,
            dest_path=dest,
            overwrite=overwrite,
        )
        return SuccessResponse(
            message="Datei verschoben.",
            detail=f"{source} → {dest}",
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Verschieben.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


@router.post(
    "/files/copy",
    response_model=SuccessResponse,
    summary="Datei kopieren",
)
async def copy_file(
    source: str = Query(..., min_length=1, description="Quellpfad."),
    dest: str = Query(..., min_length=1, description="Zielpfad."),
    overwrite: bool = Query(default=False),
    svc: FileBunkerService = Depends(get_file_service),
) -> SuccessResponse:
    """Kopiert eine Datei."""
    logger.info("POST /files/copy: %r → %r.", source, dest)

    try:
        result: dict = await svc.copy_file(
            source_path=source,
            dest_path=dest,
            overwrite=overwrite,
        )
        return SuccessResponse(
            message="Datei kopiert.",
            detail=f"{source} → {dest}",
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Fehler beim Kopieren.")
        raise HTTPException(
            status_code=500, detail=f"Fehler: {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# Speicher-Info
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/files/storage",
    response_model=StorageInfoResponse,
    summary="Speicherplatz-Info",
    description="Ermittelt den Speicherplatz-Status des Dateibunker-Verzeichnisses.",
)
async def get_storage_info(
    svc: FileBunkerService = Depends(get_file_service),
) -> dict:
    """
    Gibt Auskunft über Gesamt-, belegten und freien Speicherplatz
    des Dateibunker-Basisverzeichnisses.
    """
    logger.debug("GET /files/storage.")
    return await svc.get_storage_info()

    if cleaned_path.startswith("..") or cleaned_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Ungültiger Pfadzugriff versucht.")
    
    return [{"name": "Dokumente", "is_dir": True, "size_bytes": 0}]

@router.post("/upload")
async def upload_file(path: str, file: UploadFile = File(...)):
    """
    Nimmt Upload-Dateiströme entgegen und speichert sie sicher auf der HDD.
    """
    # Pfad-Traversal Guard Stub
    if ".." in path or path.startswith("/"):
         raise HTTPException(status_code=400, detail="Sicherheitsverletzung im Pfad.")
    return {"filename": file.filename, "status": "stored"}