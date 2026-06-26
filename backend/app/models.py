"""
Relationale Datenbankmodelle des Homelab-Imperiums.

Definiert das vollständige SQLAlchemy-ORM-Schema für alle Kernbereiche:
Finanzen, Gesundheit/Biometrie, Schule, Fahrzeuge und Wartung.

Jedes Modell nutzt die gemeinsame ``Base``-Klasse aus ``app.database``
und ist für PostgreSQL optimiert — inklusive Indizes, Foreign Keys und
Kaskadierungsregeln.

Verwendung::

    from app.database import get_db
    from app.models import Transaction

    with get_db_context() as db:
        alle = db.query(Transaction).filter_by(category="Miete").all()
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base

# ═══════════════════════════════════════════════════════════════════════════════
# Hilfs-Enum-Typen (als String-Literale — flexibel erweiterbar)
# ═══════════════════════════════════════════════════════════════════════════════

# record_type in HealthRecord
_HEALTH_RECORD_TYPES: tuple[str, ...] = (
    "weight",    # Körpergewicht in kg (val1), KFA in % (val2)
    "meal",      # Mahlzeit: Kalorien (val1), Protein in g (val2)
    "workout",   # Training: Dauer in min (val1), verbrannte kcal (val2)
    "symptom",   # Symptom: NRS-Intensität (val1), — (val2)
    "vitals",    # Vitalwerte: Blutdruck syst (val1), diast (val2)
    "sleep",     # Schlaf: Dauer in h (val1), Qualität 1–5 (val2)
    "water",     # Wasseraufnahme: Menge in ml (val1), — (val2)
    "medication",# Medikament: Dosis in mg (val1), — (val2)
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Finanzen — transactions
# ═══════════════════════════════════════════════════════════════════════════════

class Transaction(Base):
    """
    Finanzielle Transaktion — Einnahme oder Ausgabe.

    Jede Transaktion gehört zu einer Kategorie, hat einen Betrag und
    einen Zeitstempel. Ausgaben (``is_expense=True``) werden in Reports
    negativ aggregiert, Einnahmen positiv.
    """

    __tablename__ = "transactions"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Pflichtfelder ──
    amount: Decimal = Column(
        Numeric(precision=12, scale=2),
        nullable=False,
        comment="Transaktionsbetrag (positiv). Zwei Nachkommastellen.",
    )
    category: str = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Kategorie: z.B. Miete, Lebensmittel, Gehalt, Mobilität.",
    )
    is_expense: bool = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="True = Ausgabe, False = Einnahme.",
    )
    timestamp: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        comment="Zeitpunkt der Transaktion (UTC).",
    )

    # ── Optionalfelder ──
    description: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext-Beschreibung (z.B. Verwendungszweck, Notiz).",
    )
    payment_method: Optional[str] = Column(
        String(50),
        nullable=True,
        comment="Zahlungsmethode: bar, girocard, kreditkarte, paypal, überweisung.",
    )
    is_recurring: bool = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = wiederkehrende Transaktion (Abo, Dauerauftrag).",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_transactions_category_timestamp", "category", "timestamp"),
        Index("ix_transactions_is_expense_timestamp", "is_expense", "timestamp"),
        Index(
            "ix_transactions_timestamp_desc",
            text("timestamp DESC"),
        ),
        {"comment": "Finanztransaktionen des Homelab-Imperiums."},
    )

    def __repr__(self) -> str:
        typ = "Ausgabe" if self.is_expense else "Einnahme"
        return (
            f"<Transaction(id={self.id}, {typ}, "
            f"amount={self.amount}, category={self.category!r})>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Gesundheit & Biometrie — health_records
# ═══════════════════════════════════════════════════════════════════════════════

class HealthRecord(Base):
    """
    Universeller Gesundheitseintrag für Biometrie, Ernährung, Training
    und Symptome.

    Das generische Design mit ``val1``/``val2`` und ``record_type`` erlaubt
    die Speicherung unterschiedlicher Datentypen in einer einzigen Tabelle,
    ohne für jeden Typ eine eigene Tabelle anlegen zu müssen.

    Der ``symptom_location``-String korrespondiert mit dem 3D-Körper-
    Hologramm im Frontend (z.B. ``"shoulder_L"``, ``"knee_R"``).
    """

    __tablename__ = "health_records"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Pflichtfelder ──
    record_type: str = Column(
        String(20),
        nullable=False,
        index=True,
        comment="Typ des Eintrags: weight, meal, workout, symptom, vitals, "
        "sleep, water, medication.",
    )
    timestamp: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        index=True,
        comment="Zeitpunkt der Messung/Aufzeichnung (UTC).",
    )

    # ── Numerische Werte (generisch — Bedeutung hängt von record_type ab) ──
    val1: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Primärer numerischer Wert. Bedeutung je nach record_type: "
        "weight→kg, meal→kcal, workout→min, symptom→NRS(1-10), "
        "vitals→systolisch, sleep→Stunden, water→ml, medication→mg.",
    )
    val2: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Sekundärer numerischer Wert. Bedeutung je nach record_type: "
        "weight→KFA%, meal→Protein(g), workout→kcal, vitals→diastolisch, "
        "sleep→Qualität(1-5).",
    )
    val3: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Tertiärer numerischer Wert. Z.B. meal→Carbs(g), "
        "workout→avg_hr(bpm).",
    )

    # ── Beschreibung & Symptom-Lokation ──
    description: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext: Mahlzeit-Beschreibung, Übungsname, Symptom-Detail.",
    )
    symptom_location: Optional[str] = Column(
        String(30),
        nullable=True,
        index=True,
        comment="3D-Hologramm-Lokation. GÜLTIGE WERTE: head, neck, "
        "shoulder_L, shoulder_R, elbow_L, elbow_R, wrist_L, wrist_R, "
        "hand_L, hand_R, chest_L, chest_R, abdomen_UL, abdomen_UR, "
        "abdomen_LL, abdomen_LR, upper_back, lower_back, hip_L, hip_R, "
        "knee_L, knee_R, ankle_L, ankle_R, foot_L, foot_R, systemic.",
    )
    intensity: Optional[str] = Column(
        String(10),
        nullable=True,
        comment="Intensität für Hologramm-Farbe: high→rot, medium→orange, "
        "low→gelb. Nur relevant bei record_type='symptom'.",
    )

    # ── Referenz zum Fahrzeug (falls Verletzung beim Arbeiten am Fahrzeug) ──
    vehicle_id: Optional[int] = Column(
        Integer,
        ForeignKey("vehicles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Falls die Verletzung im Zusammenhang mit einem Fahrzeug steht.",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_health_record_type_ts", "record_type", "timestamp"),
        Index("ix_health_symptom_location", "symptom_location"),
        Index(
            "ix_health_timestamp_desc",
            text("timestamp DESC"),
        ),
        {"comment": "Gesundheitsdaten: Biometrie, Mahlzeiten, Training, Symptome."},
    )

    def __repr__(self) -> str:
        return (
            f"<HealthRecord(id={self.id}, type={self.record_type!r}, "
            f"ts={self.timestamp})>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Schule — school_subjects & school_grades
# ═══════════════════════════════════════════════════════════════════════════════

class SchoolSubject(Base):
    """
    Unterrichtsfach der Berufsschule (Fachinformatiker AE).

    Hat eine 1:n-Beziehung zu ``SchoolGrade`` (ein Fach hat viele Noten)
    und eine 1:n-Beziehung zu ``SchoolDeadline`` (Abgabetermine).
    """

    __tablename__ = "school_subjects"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Pflichtfelder ──
    name: str = Column(
        String(150),
        unique=True,
        nullable=False,
        index=True,
        comment="Name des Fachs, z.B. 'IT-Systeme', 'Anwendungsentwicklung', "
        "'Wirtschafts- und Sozialkunde'.",
    )

    # ── Optionalfelder ──
    teacher: Optional[str] = Column(
        String(150),
        nullable=True,
        comment="Name der unterrichtenden Lehrkraft.",
    )
    room: Optional[str] = Column(
        String(50),
        nullable=True,
        comment="Klassenraum, z.B. 'A1.02'.",
    )
    is_exam_subject: bool = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = Prüfungsfach (relevant für AP1/AP2).",
    )
    color_hex: Optional[str] = Column(
        String(7),
        nullable=True,
        comment="Hex-Farbe für die UI-Darstellung, z.B. '#a06bff'.",
    )

    # ── Beziehungen ──
    grades: list[SchoolGrade] = relationship(
        "SchoolGrade",
        back_populates="subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",  # Reduziert N+1-Queries bei Fach+Noten-Abfragen
        order_by="SchoolGrade.date.desc()",
    )
    deadlines: list[SchoolDeadline] = relationship(
        "SchoolDeadline",
        back_populates="subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="SchoolDeadline.due_date.asc()",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_school_subjects_name", "name"),
        {"comment": "Unterrichtsfächer der Berufsschule."},
    )

    def __repr__(self) -> str:
        return (
            f"<SchoolSubject(id={self.id}, name={self.name!r}, "
            f"teacher={self.teacher!r})>"
        )


class SchoolGrade(Base):
    """
    Einzelne Note oder Leistungsbewertung innerhalb eines Fachs.

    Jede Note gehört zu genau einem ``SchoolSubject`` (n:1) und hat
    eine Gewichtung (z.B. Klassenarbeit = höhere Gewichtung als Test).
    """

    __tablename__ = "school_grades"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Fremdschlüssel ──
    subject_id: int = Column(
        Integer,
        ForeignKey(
            "school_subjects.id",
            ondelete="CASCADE",  # Fach löschen → alle Noten löschen
        ),
        nullable=False,
        index=True,
        comment="Referenz auf das zugehörige Unterrichtsfach.",
    )

    # ── Pflichtfelder ──
    value: float = Column(
        Float,
        nullable=False,
        comment="Notenwert. Deutsches System: 1,0 (sehr gut) bis 6,0 (ungenügend). "
        "Zwischenwerte in 0,5er-Schritten möglich, z.B. 2,5.",
    )

    # ── Optionalfelder ──
    weight: float = Column(
        Float,
        default=1.0,
        nullable=False,
        comment="Gewichtung der Note. 1,0 = einfach, 2,0 = doppelt "
        "(z.B. Klassenarbeit).",
    )
    grade_type: Optional[str] = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Art der Leistungserhebung: klassenarbeit, test, "
        "mündlich, projekt, klausur, referat.",
    )
    description: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext: Thema der Arbeit, Bemerkungen des Lehrers.",
    )
    date: Optional[date] = Column(
        Date,
        nullable=True,
        comment="Datum der Leistungserhebung.",
    )

    # ── Beziehungen ──
    subject: SchoolSubject = relationship(
        "SchoolSubject",
        back_populates="grades",
    )

    # ── Constraints & Indizes ──
    __table_args__ = (
        Index("ix_school_grades_subject_date", "subject_id", "date"),
        {"comment": "Noten und Leistungsbewertungen pro Fach."},
    )

    def __repr__(self) -> str:
        return (
            f"<SchoolGrade(id={self.id}, subject_id={self.subject_id}, "
            f"value={self.value}, weight={self.weight})>"
        )


class SchoolDeadline(Base):
    """
    Abgabetermin oder Prüfungstermin für ein Fach.

    Jeder Termin gehört zu genau einem ``SchoolSubject`` (n:1).
    """

    __tablename__ = "school_deadlines"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Fremdschlüssel ──
    subject_id: int = Column(
        Integer,
        ForeignKey("school_subjects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Referenz auf das zugehörige Unterrichtsfach.",
    )

    # ── Pflichtfelder ──
    title: str = Column(
        String(200),
        nullable=False,
        comment="Titel des Termins, z.B. 'AP1-Prüfung' oder "
        "'Projektdokumentation abgeben'.",
    )
    due_date: date = Column(
        Date,
        nullable=False,
        index=True,
        comment="Fälligkeitsdatum.",
    )

    # ── Optionalfelder ──
    deadline_type: Optional[str] = Column(
        String(50),
        nullable=True,
        comment="Typ: abgabe, prüfung, klassenarbeit, referat, sonstiges.",
    )
    is_completed: bool = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = erledigt.",
    )
    priority: Optional[str] = Column(
        String(10),
        nullable=True,
        comment="Priorität: high, medium, low.",
    )
    notes: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext-Notizen.",
    )

    # ── Beziehungen ──
    subject: SchoolSubject = relationship(
        "SchoolSubject",
        back_populates="deadlines",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_school_deadlines_due_date", "due_date"),
        Index("ix_school_deadlines_subject_due", "subject_id", "due_date"),
        {"comment": "Abgabe- und Prüfungstermine."},
    )

    def __repr__(self) -> str:
        return (
            f"<SchoolDeadline(id={self.id}, title={self.title!r}, "
            f"due={self.due_date})>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Fahrzeuge — vehicles & maintenance_records
# ═══════════════════════════════════════════════════════════════════════════════

class Vehicle(Base):
    """
    Fahrzeug-Stammdaten: Autos und Motorräder.

    Speichert Basisdaten, Kilometerstand, Wartungsfälligkeiten und
    Schadenszustände. Hat eine 1:n-Beziehung zu ``MaintenanceRecord``.
    """

    __tablename__ = "vehicles"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Pflichtfelder ──
    name: str = Column(
        String(150),
        nullable=False,
        index=True,
        comment="Name/Modellbezeichnung, z.B. 'BMW 330e Touring (G21)'.",
    )
    vehicle_type: str = Column(
        String(20),
        nullable=False,
        index=True,
        comment="Fahrzeugtyp: car, bike, truck, motorcycle, ev, hybrid.",
    )

    # ── Basis-Kennzahlen ──
    odometer_km: float = Column(
        Float,
        default=0.0,
        nullable=False,
        comment="Aktueller Kilometerstand.",
    )
    year_of_manufacture: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Baujahr.",
    )
    license_plate: Optional[str] = Column(
        String(20),
        nullable=True,
        comment="Amtliches Kennzeichen.",
    )
    vin: Optional[str] = Column(
        String(50),
        nullable=True,
        comment="Fahrzeugidentifikationsnummer (VIN), 17 Zeichen.",
    )

    # ── Wartungs-Fälligkeiten ──
    oil_change_due_km: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Kilometerstand, bei dem der nächste Ölwechsel fällig ist.",
    )
    oil_change_due_date: Optional[date] = Column(
        Date,
        nullable=True,
        comment="Datum, bis zu dem der nächste Ölwechsel fällig ist "
        "(falls zeitbasiert).",
    )
    tire_change_due_date: Optional[date] = Column(
        Date,
        nullable=True,
        comment="Datum, bis zu dem der nächste Reifenwechsel fällig ist.",
    )
    inspection_due_date: Optional[date] = Column(
        Date,
        nullable=True,
        comment="Datum der nächsten Hauptuntersuchung (HU/TÜV).",
    )

    # ── Schadensstatus ──
    is_damaged: bool = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = Fahrzeug hat aktive Schäden.",
    )
    damaged_parts_json: Optional[str] = Column(
        Text,
        nullable=True,
        comment="JSON-Array der beschädigten Komponenten für das 3D-Modell, "
        "z.B. '[\"engine\", \"brake_F\", \"door_R\"]'.",
    )

    # ── 3D-Modell & Medien ──
    model_3d_path: Optional[str] = Column(
        String(500),
        nullable=True,
        comment="Relativer Pfad zum Three.js/GLTF-3D-Modell.",
    )
    image_path: Optional[str] = Column(
        String(500),
        nullable=True,
        comment="Pfad zum Bild des Fahrzeugs.",
    )

    # ── Notizen ──
    notes: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext-Notizen (Besonderheiten, Historie).",
    )

    # ── Zeitstempel ──
    created_at: datetime = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="Erstellungszeitpunkt (UTC).",
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="Letzte Änderung (UTC).",
    )

    # ── Beziehungen ──
    maintenance_records: list[MaintenanceRecord] = relationship(
        "MaintenanceRecord",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="MaintenanceRecord.date.desc()",
    )
    health_records: list[HealthRecord] = relationship(
        "HealthRecord",
        back_populates=None,  # Kein Back-Populate, HealthRecord hat vehicle_id
        foreign_keys="[HealthRecord.vehicle_id]",
        lazy="selectin",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_vehicles_name_type", "name", "vehicle_type"),
        Index("ix_vehicles_is_damaged", "is_damaged"),
        {"comment": "Fahrzeug-Stammdaten des Homelab-Imperiums."},
    )

    def __repr__(self) -> str:
        return (
            f"<Vehicle(id={self.id}, name={self.name!r}, "
            f"type={self.vehicle_type!r}, odo={self.odometer_km})>"
        )


class MaintenanceRecord(Base):
    """
    Einzelner Wartungseintrag für ein Fahrzeug.

    Jeder Eintrag gehört zu genau einem ``Vehicle`` (n:1). Dokumentiert
    durchgeführte und geplante Wartungsarbeiten mit Kosten und Intervallen.
    """

    __tablename__ = "maintenance_records"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Fremdschlüssel ──
    vehicle_id: int = Column(
        Integer,
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Referenz auf das zugehörige Fahrzeug.",
    )

    # ── Pflichtfelder ──
    maintenance_type: str = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Art der Wartung: oil_change, tire_change, inspection, "
        "brake_change, repair, general_service, tuning.",
    )
    description: str = Column(
        String(500),
        nullable=False,
        comment="Beschreibung der durchgeführten Arbeit.",
    )
    date: date = Column(
        Date,
        nullable=False,
        index=True,
        comment="Datum der Wartung.",
    )

    # ── Optionalfelder ──
    odometer_at_service_km: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Kilometerstand zum Zeitpunkt der Wartung.",
    )
    cost_eur: Optional[Decimal] = Column(
        Numeric(precision=10, scale=2),
        nullable=True,
        comment="Kosten der Wartung in Euro.",
    )
    workshop: Optional[str] = Column(
        String(200),
        nullable=True,
        comment="Name der Werkstatt / des Dienstleisters.",
    )
    next_due_km: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Kilometerstand für die nächste Wiederholung.",
    )
    next_due_date: Optional[date] = Column(
        Date,
        nullable=True,
        comment="Datum für die nächste Wiederholung.",
    )
    invoice_path: Optional[str] = Column(
        String(500),
        nullable=True,
        comment="Pfad zur Rechnung / zum Beleg (PDF).",
    )
    notes: Optional[str] = Column(
        Text,
        nullable=True,
        comment="Freitext-Notizen, Hinweise für die nächste Wartung.",
    )

    # ── Beziehungen ──
    vehicle: Vehicle = relationship(
        "Vehicle",
        back_populates="maintenance_records",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_maintenance_vehicle_date", "vehicle_id", "date"),
        Index("ix_maintenance_type", "maintenance_type"),
        {"comment": "Wartungshistorie pro Fahrzeug."},
    )

    def __repr__(self) -> str:
        return (
            f"<MaintenanceRecord(id={self.id}, vehicle_id={self.vehicle_id}, "
            f"type={self.maintenance_type!r}, date={self.date})>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Musik — music_tracks
# ═══════════════════════════════════════════════════════════════════════════════

class MusicTrack(Base):
    """
    Einzelner Musiktitel in der lokalen MP3-Sammlung.

    Speichert ID3-Metadaten (Titel, Artist, Album) und Dateipfade.
    """

    __tablename__ = "music_tracks"

    # ── Primärschlüssel ──
    id: int = Column(Integer, primary_key=True, autoincrement=True, index=True)

    # ── Pflichtfelder ──
    title: str = Column(
        String(300),
        nullable=False,
        index=True,
        comment="Titel des Musikstücks.",
    )
    file_path: str = Column(
        String(1000),
        unique=True,
        nullable=False,
        comment="Absoluter Pfad zur MP3-Datei auf dem Server.",
    )

    # ── ID3-Metadaten ──
    artist: Optional[str] = Column(
        String(300),
        nullable=True,
        index=True,
        comment="Interpret / Band.",
    )
    album: Optional[str] = Column(
        String(300),
        nullable=True,
        index=True,
        comment="Album-Name.",
    )
    album_artist: Optional[str] = Column(
        String(300),
        nullable=True,
        comment="Album-Interpret (falls abweichend).",
    )
    genre: Optional[str] = Column(
        String(100),
        nullable=True,
        index=True,
        comment="Genre, z.B. 'Rock', 'Jazz', 'Electronic'.",
    )
    track_number: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Track-Nummer innerhalb des Albums (1/12, 2/12, ...).",
    )
    total_tracks: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Gesamtzahl der Tracks im Album.",
    )
    disc_number: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="CD-Nummer bei Multi-Disc-Alben.",
    )
    year: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Veröffentlichungsjahr.",
    )
    duration_seconds: Optional[float] = Column(
        Float,
        nullable=True,
        comment="Dauer in Sekunden.",
    )
    bitrate_kbps: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Bitrate in kbps, z.B. 320.",
    )
    sample_rate_hz: Optional[int] = Column(
        Integer,
        nullable=True,
        comment="Samplerate in Hz, z.B. 44100.",
    )

    # ── Cover-Art ──
    has_cover_art: bool = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = Cover-Art ist im MP3-Tag eingebettet.",
    )
    cover_art_path: Optional[str] = Column(
        String(1000),
        nullable=True,
        comment="Pfad zur extrahierten Cover-Art-Datei (JPEG/PNG).",
    )

    # ── Indizes ──
    __table_args__ = (
        Index("ix_music_artist_album", "artist", "album"),
        Index("ix_music_genre", "genre"),
        {"comment": "Lokale MP3-Musiksammlung mit ID3-Metadaten."},
    )

    def __repr__(self) -> str:
        return (
            f"<MusicTrack(id={self.id}, title={self.title!r}, "
            f"artist={self.artist!r})>"
        )
