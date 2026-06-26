"""
Pydantic-Schemas des Homelab-Imperiums.

Definiert typsichere Datenübertragungsobjekte (DTOs) für:
- Eingabevalidierung (Create/Update-Schemas)
- Ausgabeserialisierung (Response-Schemas)
- API-Request/Response-Payloads (Chat, Health, System)

Jedes Schema nutzt strikte Pydantic-Typen (``PositiveFloat``, ``PastDate``,
``constr``, …) zur Eingabeverifizierung. Response-Schemas verwenden
``from_attributes=True`` für die direkte ORM→Pydantic-Konvertierung.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PastDate,
    PositiveFloat,
    StringConstraints,
    field_validator,
    model_validator,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Wiederverwendbare Typ-Aliase & Constraints
# ═══════════════════════════════════════════════════════════════════════════════

# String mit Mindestlänge, automatisch gestrippt
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

# Deutsche Note: 1,0 bis 6,0 in 0,5er-Schritten (z.B. 1,0, 1,5, 2,0, …)
GermanGrade = Annotated[float, Field(ge=1.0, le=6.0, multiple_of=0.5)]

# Prozentwert 0–100
Percentage = Annotated[float, Field(ge=0.0, le=100.0)]

# Positiver Geldbetrag
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]

# Gültige Hologramm-Lokationen (aus der medical_health.yaml)
HOLOGRAM_LOCATION = Annotated[
    str,
    Field(
        pattern=r"^(head|temple_[LR]|jaw_[LR]|neck|throat|chest|chest_[LR]|"
        r"abdomen|abdomen_U[LR]|abdomen_L[LR]|groin_[LR]|upper_back|"
        r"lower_back|spine_cervical|spine_thoracic|spine_lumbar|"
        r"shoulder_[LR]|upper_arm_[LR]|elbow_[LR]|forearm_[LR]|"
        r"wrist_[LR]|hand_[LR]|hip_[LR]|thigh_[LR]|knee_[LR]|"
        r"shin_[LR]|calf_[LR]|ankle_[LR]|foot_[LR]|systemic)$"
    ),
]

# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class HealthRecordType(str, Enum):
    """Gültige Typen für health_records.record_type."""

    WEIGHT = "weight"
    MEAL = "meal"
    WORKOUT = "workout"
    SYMPTOM = "symptom"
    VITALS = "vitals"
    SLEEP = "sleep"
    WATER = "water"
    MEDICATION = "medication"


class SymptomIntensity(str, Enum):
    """Intensitätsstufen für das 3D-Hologramm."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VehicleType(str, Enum):
    """Fahrzeugtypen."""

    CAR = "car"
    BIKE = "bike"
    MOTORCYCLE = "motorcycle"
    TRUCK = "truck"
    EV = "ev"
    HYBRID = "hybrid"


class MaintenanceType(str, Enum):
    """Wartungstypen."""

    OIL_CHANGE = "oil_change"
    TIRE_CHANGE = "tire_change"
    INSPECTION = "inspection"
    BRAKE_CHANGE = "brake_change"
    REPAIR = "repair"
    GENERAL_SERVICE = "general_service"
    TUNING = "tuning"


class GradeType(str, Enum):
    """Typen von Leistungserhebungen."""

    KLASSENARBEIT = "klassenarbeit"
    TEST = "test"
    MUENDLICH = "mündlich"
    PROJEKT = "projekt"
    KLAUSUR = "klausur"
    REFERAT = "referat"


class DeadlineType(str, Enum):
    """Typen von Abgabe-/Prüfungsterminen."""

    ABGABE = "abgabe"
    PRUEFUNG = "prüfung"
    KLASSENARBEIT = "klassenarbeit"
    REFERAT = "referat"
    SONSTIGES = "sonstiges"


class Priority(str, Enum):
    """Prioritätsstufen."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentName(str, Enum):
    """Verfügbare Spezial-Agenten im Homelab-Imperium."""

    IT_TUTOR = "it_tutor"
    AUTO_ENGINEER = "auto_engineer"
    MEDICAL_HEALTH = "medical_health"
    BRAINSTORM = "brainstorm_agent"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Transaction — Finanzen
# ═══════════════════════════════════════════════════════════════════════════════


class TransactionCreate(BaseModel):
    """
    Eingabeschema für das Erstellen einer Finanztransaktion.

    Alle Felder außer ``description`` und ``payment_method`` sind Pflicht.
    """

    amount: PositiveFloat = Field(
        ...,
        description="Transaktionsbetrag (positiv). Zwei Nachkommastellen.",
    )
    category: NonEmptyStr = Field(
        ...,
        max_length=100,
        description="Kategorie, z.B. 'Miete', 'Lebensmittel', 'Gehalt'.",
    )
    is_expense: bool = Field(
        default=True,
        description="True = Ausgabe, False = Einnahme.",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Zeitpunkt der Transaktion (UTC).",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Freitext-Beschreibung.",
    )
    payment_method: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Zahlungsmethode.",
    )
    is_recurring: bool = Field(
        default=False,
        description="True = wiederkehrende Transaktion.",
    )


class TransactionUpdate(BaseModel):
    """
    Eingabeschema für partielle Aktualisierung (PATCH).

    Alle Felder sind optional — nur gesendete Felder werden aktualisiert.
    """

    amount: Optional[PositiveFloat] = Field(default=None)
    category: Optional[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default=None)  # noqa: E501
    is_expense: Optional[bool] = Field(default=None)
    timestamp: Optional[datetime] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=2000)
    payment_method: Optional[str] = Field(default=None, max_length=50)
    is_recurring: Optional[bool] = Field(default=None)


class TransactionResponse(BaseModel):
    """Ausgabeschema für eine Finanztransaktion (ORM-kompatibel)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    category: str
    is_expense: bool
    timestamp: datetime
    description: Optional[str] = None
    payment_method: Optional[str] = None
    is_recurring: bool = False


class TransactionSummary(BaseModel):
    """
    Aggregierte Finanzübersicht (Einnahmen vs. Ausgaben).
    """

    total_income: Decimal = Field(default=Decimal("0.00"))
    total_expenses: Decimal = Field(default=Decimal("0.00"))
    net_balance: Decimal = Field(default=Decimal("0.00"))
    transaction_count: int = 0
    top_category: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HealthRecord — Gesundheit & Biometrie
# ═══════════════════════════════════════════════════════════════════════════════


class HealthRecordCreate(BaseModel):
    """
    Eingabeschema für einen Gesundheitseintrag.

    ``record_type`` bestimmt die Bedeutung der numerischen Felder.
    """

    record_type: HealthRecordType = Field(
        ...,
        description="Typ: weight, meal, workout, symptom, vitals, "
        "sleep, water, medication.",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Zeitpunkt der Messung (UTC).",
    )
    val1: Optional[float] = Field(
        default=None,
        description="Primärer numerischer Wert (kontextabhängig).",
    )
    val2: Optional[float] = Field(
        default=None,
        description="Sekundärer numerischer Wert (kontextabhängig).",
    )
    val3: Optional[float] = Field(
        default=None,
        description="Tertiärer numerischer Wert (kontextabhängig).",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Freitext-Beschreibung.",
    )
    symptom_location: Optional[HOLOGRAM_LOCATION] = Field(
        default=None,
        description="3D-Hologramm-Lokation, z.B. 'shoulder_L'. "
        "Nur bei record_type='symptom'.",
    )
    intensity: Optional[SymptomIntensity] = Field(
        default=None,
        description="Intensität für Hologramm-Farbe. Nur bei symptom.",
    )
    vehicle_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Referenz auf ein Fahrzeug (falls verletzungsrelevant).",
    )

    @model_validator(mode="after")
    def _validate_symptom_fields(self) -> "HealthRecordCreate":
        """Erzwingt symptom_location und intensity nur bei symptom-Typ."""
        if self.record_type == HealthRecordType.SYMPTOM:
            if self.symptom_location is None:
                raise ValueError(
                    "symptom_location ist Pflicht bei record_type='symptom'."
                )
            if self.intensity is None:
                raise ValueError(
                    "intensity ist Pflicht bei record_type='symptom' "
                    "(high/medium/low)."
                )
        return self


class HealthRecordUpdate(BaseModel):
    """Partielle Aktualisierung eines Gesundheitseintrags."""

    val1: Optional[float] = Field(default=None)
    val2: Optional[float] = Field(default=None)
    val3: Optional[float] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=2000)
    intensity: Optional[SymptomIntensity] = Field(default=None)


class HealthRecordResponse(BaseModel):
    """Ausgabeschema für einen Gesundheitseintrag."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    record_type: str
    timestamp: datetime
    val1: Optional[float] = None
    val2: Optional[float] = None
    val3: Optional[float] = None
    description: Optional[str] = None
    symptom_location: Optional[str] = None
    intensity: Optional[str] = None
    vehicle_id: Optional[int] = None


class HologramStatusResponse(BaseModel):
    """
    Status des 3D-Körper-Hologramms.

    Liste aller aktiven Anomalien mit Lokation, Intensität und Ursache.
    """

    anomalies: list["HologramAnomaly"] = Field(
        default_factory=list,
        description="Liste aller aktiven Symptome/Verletzungen.",
    )


class HologramAnomaly(BaseModel):
    """Einzelne Anomalie im 3D-Hologramm."""

    location: HOLOGRAM_LOCATION = Field(
        ...,
        description="Anatomische Lokation (z.B. 'knee_L').",
    )
    intensity: SymptomIntensity = Field(
        ...,
        description="high→rot, medium→orange, low→gelb.",
    )
    cause: str = Field(
        ...,
        max_length=500,
        description="Beschreibung der Ursache (z.B. 'Rotatorenmanschette Reizung').",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SchoolSubject & SchoolGrade — Schule
# ═══════════════════════════════════════════════════════════════════════════════


class SchoolSubjectCreate(BaseModel):
    """Eingabeschema für ein neues Unterrichtsfach."""

    name: NonEmptyStr = Field(
        ...,
        max_length=150,
        description="Name des Fachs.",
    )
    teacher: Optional[str] = Field(default=None, max_length=150)
    room: Optional[str] = Field(default=None, max_length=50)
    is_exam_subject: bool = Field(default=False)
    color_hex: Optional[str] = Field(
        default=None,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="Hex-Farbe, z.B. '#a06bff'.",
    )


class SchoolSubjectUpdate(BaseModel):
    """Partielle Aktualisierung eines Fachs."""

    name: Optional[Annotated[str, StringConstraints(min_length=1, max_length=150)]] = Field(default=None)  # noqa: E501
    teacher: Optional[str] = Field(default=None, max_length=150)
    room: Optional[str] = Field(default=None, max_length=50)
    is_exam_subject: Optional[bool] = Field(default=None)
    color_hex: Optional[str] = Field(
        default=None,
        pattern=r"^#[0-9a-fA-F]{6}$",
    )


class SchoolGradeResponse(BaseModel):
    """Ausgabeschema für eine Note."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    value: float
    weight: float = 1.0
    grade_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[date] = None


class SchoolDeadlineResponse(BaseModel):
    """Ausgabeschema für einen Abgabetermin."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    title: str
    due_date: date
    deadline_type: Optional[str] = None
    is_completed: bool = False
    priority: Optional[str] = None
    notes: Optional[str] = None


class SchoolSubjectResponse(BaseModel):
    """
    Ausgabeschema für ein Fach — inklusive eingebetteter Noten und Termine.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    teacher: Optional[str] = None
    room: Optional[str] = None
    is_exam_subject: bool = False
    color_hex: Optional[str] = None
    grades: list[SchoolGradeResponse] = Field(default_factory=list)
    deadlines: list[SchoolDeadlineResponse] = Field(default_factory=list)


class SchoolGradeCreate(BaseModel):
    """Eingabeschema für eine neue Note."""

    subject_id: int = Field(..., ge=1, description="ID des zugehörigen Fachs.")
    value: GermanGrade = Field(
        ...,
        description="Note: 1,0–6,0 in 0,5er-Schritten.",
    )
    weight: float = Field(default=1.0, ge=0.5, le=4.0)
    grade_type: Optional[GradeType] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=1000)
    date: Optional[PastDate] = Field(default=None)


class SchoolGradeUpdate(BaseModel):
    """Partielle Aktualisierung einer Note."""

    value: Optional[GermanGrade] = Field(default=None)
    weight: Optional[float] = Field(default=None, ge=0.5, le=4.0)
    grade_type: Optional[GradeType] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=1000)
    date: Optional[date] = Field(default=None)


class SchoolDeadlineCreate(BaseModel):
    """Eingabeschema für einen neuen Abgabetermin."""

    subject_id: int = Field(..., ge=1)
    title: NonEmptyStr = Field(..., max_length=200)
    due_date: date = Field(..., description="Fälligkeitsdatum.")
    deadline_type: Optional[DeadlineType] = Field(default=None)
    is_completed: bool = Field(default=False)
    priority: Optional[Priority] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=2000)


class SchoolDeadlineUpdate(BaseModel):
    """Partielle Aktualisierung eines Termins."""

    title: Optional[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(default=None)  # noqa: E501
    due_date: Optional[date] = Field(default=None)
    deadline_type: Optional[DeadlineType] = Field(default=None)
    is_completed: Optional[bool] = Field(default=None)
    priority: Optional[Priority] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=2000)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Vehicle & MaintenanceRecord — Fahrzeuge
# ═══════════════════════════════════════════════════════════════════════════════


class VehicleCreate(BaseModel):
    """Eingabeschema für ein neues Fahrzeug."""

    name: NonEmptyStr = Field(..., max_length=150)
    vehicle_type: VehicleType = Field(...)
    odometer_km: float = Field(default=0.0, ge=0.0)
    year_of_manufacture: Optional[int] = Field(default=None, ge=1886, le=2100)
    license_plate: Optional[str] = Field(default=None, max_length=20)
    vin: Optional[str] = Field(default=None, max_length=50)
    oil_change_due_km: Optional[float] = Field(default=None, ge=0.0)
    oil_change_due_date: Optional[date] = Field(default=None)
    tire_change_due_date: Optional[date] = Field(default=None)
    inspection_due_date: Optional[date] = Field(default=None)
    is_damaged: bool = Field(default=False)
    damaged_parts_json: Optional[str] = Field(default=None, max_length=5000)
    model_3d_path: Optional[str] = Field(default=None, max_length=500)
    image_path: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=5000)


class VehicleUpdate(BaseModel):
    """Partielle Aktualisierung eines Fahrzeugs."""

    name: Optional[Annotated[str, StringConstraints(min_length=1, max_length=150)]] = Field(default=None)  # noqa: E501
    vehicle_type: Optional[VehicleType] = Field(default=None)
    odometer_km: Optional[float] = Field(default=None, ge=0.0)
    year_of_manufacture: Optional[int] = Field(default=None, ge=1886, le=2100)
    license_plate: Optional[str] = Field(default=None, max_length=20)
    vin: Optional[str] = Field(default=None, max_length=50)
    oil_change_due_km: Optional[float] = Field(default=None, ge=0.0)
    oil_change_due_date: Optional[date] = Field(default=None)
    tire_change_due_date: Optional[date] = Field(default=None)
    inspection_due_date: Optional[date] = Field(default=None)
    is_damaged: Optional[bool] = Field(default=None)
    damaged_parts_json: Optional[str] = Field(default=None, max_length=5000)
    model_3d_path: Optional[str] = Field(default=None, max_length=500)
    image_path: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=5000)


class MaintenanceRecordResponse(BaseModel):
    """Ausgabeschema für einen Wartungseintrag."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    maintenance_type: str
    description: str
    date: date
    odometer_at_service_km: Optional[float] = None
    cost_eur: Optional[Decimal] = None
    workshop: Optional[str] = None
    next_due_km: Optional[float] = None
    next_due_date: Optional[date] = None
    invoice_path: Optional[str] = None
    notes: Optional[str] = None


class VehicleResponse(BaseModel):
    """
    Ausgabeschema für ein Fahrzeug — inklusive Wartungshistorie.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    vehicle_type: str
    odometer_km: float
    year_of_manufacture: Optional[int] = None
    license_plate: Optional[str] = None
    vin: Optional[str] = None
    oil_change_due_km: Optional[float] = None
    oil_change_due_date: Optional[date] = None
    tire_change_due_date: Optional[date] = None
    inspection_due_date: Optional[date] = None
    is_damaged: bool = False
    damaged_parts_json: Optional[str] = None
    model_3d_path: Optional[str] = None
    image_path: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    maintenance_records: list[MaintenanceRecordResponse] = Field(
        default_factory=list
    )


class MaintenanceRecordCreate(BaseModel):
    """Eingabeschema für einen neuen Wartungseintrag."""

    vehicle_id: int = Field(..., ge=1)
    maintenance_type: MaintenanceType = Field(...)
    description: NonEmptyStr = Field(..., max_length=500)
    date: PastDate = Field(
        default_factory=date.today,
        description="Datum der Wartung.",
    )
    odometer_at_service_km: Optional[float] = Field(default=None, ge=0.0)
    cost_eur: Optional[PositiveFloat] = Field(default=None)
    workshop: Optional[str] = Field(default=None, max_length=200)
    next_due_km: Optional[float] = Field(default=None, ge=0.0)
    next_due_date: Optional[date] = Field(default=None)
    invoice_path: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)


class MaintenanceRecordUpdate(BaseModel):
    """Partielle Aktualisierung eines Wartungseintrags."""

    maintenance_type: Optional[MaintenanceType] = Field(default=None)
    description: Optional[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(default=None)  # noqa: E501
    date: Optional[date] = Field(default=None)
    odometer_at_service_km: Optional[float] = Field(default=None, ge=0.0)
    cost_eur: Optional[PositiveFloat] = Field(default=None)
    workshop: Optional[str] = Field(default=None, max_length=200)
    next_due_km: Optional[float] = Field(default=None, ge=0.0)
    next_due_date: Optional[date] = Field(default=None)
    invoice_path: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MusicTrack — Musik
# ═══════════════════════════════════════════════════════════════════════════════


class MusicTrackResponse(BaseModel):
    """Ausgabeschema für einen Musiktitel."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    file_path: str
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    genre: Optional[str] = None
    track_number: Optional[int] = None
    total_tracks: Optional[int] = None
    disc_number: Optional[int] = None
    year: Optional[int] = None
    duration_seconds: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    has_cover_art: bool = False
    cover_art_path: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# 6. System-Metriken
# ═══════════════════════════════════════════════════════════════════════════════


class SystemMetricResponse(BaseModel):
    """
    Echtzeit-Systemmetriken des HP-Servers.

    Wird vom ``system``-Router periodisch abgefragt und im Dashboard
    dargestellt (CPU-Gauge, RAM-Balken, Disk-Donut-Chart).
    """

    cpu_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="CPU-Auslastung in Prozent (0–100).",
    )
    cpu_count: int = Field(
        ...,
        ge=1,
        description="Anzahl der logischen CPU-Kerne.",
    )
    ram_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="RAM-Auslastung in Prozent.",
    )
    ram_used_gb: float = Field(
        ...,
        ge=0.0,
        description="Belegter RAM in GB.",
    )
    ram_total_gb: float = Field(
        ...,
        ge=0.0,
        description="Gesamter RAM in GB.",
    )
    disk_free_gb: float = Field(
        ...,
        ge=0.0,
        description="Freier Festplattenspeicher in GB.",
    )
    disk_total_gb: float = Field(
        ...,
        ge=0.0,
        description="Gesamter Festplattenspeicher in GB.",
    )
    disk_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Festplattenbelegung in Prozent.",
    )
    uptime: str = Field(
        ...,
        description="System-Uptime als menschenlesbarer String, z.B. '5d 3h 12m'.",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Zeitpunkt der Messung (UTC).",
    )


class DatabaseHealthResponse(BaseModel):
    """Health-Check-Antwort der Datenbank."""

    status: str = Field(..., description="'healthy' oder 'unhealthy'.")
    response_time_ms: float = Field(..., description="Antwortzeit in ms.")
    pool_size: Optional[int] = Field(default=None)
    checked_out: Optional[int] = Field(default=None)
    overflow: Optional[int] = Field(default=None)
    error: Optional[str] = Field(default=None)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. KI-Chat — Request/Response
# ═══════════════════════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    """
    Eingabeschema für eine KI-Chat-Anfrage.

    Wird vom ``ai``-Router an den ``OllamaSmartRouter`` delegiert, der
    basierend auf ``power_mode`` und GPU-Verfügbarkeit den Inferenz-Knoten
    wählt (CPU vs. GPU-Desktop).
    """

    agent_name: AgentName = Field(
        ...,
        description="Ziel-Agent: it_tutor, auto_engineer, medical_health, "
        "brainstorm_agent.",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Die eigentliche Nutzerfrage / Prompt.",
    )
    power_mode: bool = Field(
        default=False,
        description="True = GPU-beschleunigte Inferenz bevorzugen "
        "(fällt auf CPU zurück bei Nichterreichbarkeit).",
    )
    rag_enabled: bool = Field(
        default=True,
        description="True = RAG-Kontext aus ChromaDB anreichern.",
    )
    streaming: bool = Field(
        default=False,
        description="True = Token-für-Token Streaming-Response.",
    )

    @field_validator("prompt")
    @classmethod
    def _sanitize_prompt(cls, v: str) -> str:
        """Entfernt potenzielle Prompt-Injection-Muster."""
        lower = v.lower()
        blocked = ("ignore previous", "system prompt:", "### instruction")
        for pattern in blocked:
            if pattern in lower:
                raise ValueError(
                    f"Prompt enthält blockiertes Muster: {pattern!r}"
                )
        return v.strip()


class ChatResponse(BaseModel):
    """
    Ausgabeschema für eine KI-Chat-Antwort.

    Enthält neben der Antworttext auch Metriken zur Inferenz-Performance
    (Antwortzeit, Tokens/s, genutztes Backend).
    """

    response: str = Field(
        ...,
        description="Generierter Antworttext des Agenten.",
    )
    backend_used: str = Field(
        ...,
        description="Genutztes Backend: 'HP-Server (CPU)' oder "
        "'Desktop-PC (GPU)'.",
    )
    model: str = Field(
        ...,
        description="Verwendetes LLM-Modell, z.B. 'qwen2.5-coder:7b'.",
    )
    execution_time_ms: float = Field(
        ...,
        ge=0.0,
        description="Inferenz-Dauer in Millisekunden.",
    )
    tokens_per_second: float = Field(
        ...,
        ge=0.0,
        description="Generierungsgeschwindigkeit in Tokens/Sekunde.",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="RAG-Quellen, die für die Antwort genutzt wurden "
        "(z.B. PDF-Dateinamen).",
    )
    total_tokens: Optional[int] = Field(
        default=None,
        description="Gesamtzahl der generierten Tokens.",
    )


class AgentInfo(BaseModel):
    """Metadaten eines registrierten Agenten (für die UI)."""

    agent_name: str
    display_name: str
    description: str
    avatar_gradient: str
    default_model: str
    temperature: float
    is_available: bool = True


class AgentsListResponse(BaseModel):
    """Liste aller verfügbaren Agenten für die Chat-UI."""

    agents: list[AgentInfo]


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Paginierung & generische Responses
# ═══════════════════════════════════════════════════════════════════════════════


class PaginationParams(BaseModel):
    """Query-Parameter für paginierte Endpunkte."""

    page: int = Field(default=1, ge=1, description="Seitennummer (1-basiert).")
    page_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Einträge pro Seite.",
    )


class PaginatedResponse(BaseModel):
    """Generische paginierte Antwort."""

    items: list[Any] = Field(default_factory=list)
    total: int = Field(default=0, description="Gesamtzahl der Einträge.")
    page: int = Field(default=1)
    page_size: int = Field(default=50)
    total_pages: int = Field(default=0)


class ErrorResponse(BaseModel):
    """Standardisiertes Fehlerschema für alle API-Routen."""

    detail: str = Field(..., description="Menschenlesbare Fehlerbeschreibung.")
    status_code: int = Field(..., description="HTTP-Statuscode.")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Zeitpunkt des Fehlers (UTC).",
    )


class SuccessMessage(BaseModel):
    """Einfache Erfolgsmeldung (z.B. nach DELETE)."""

    message: str = Field(default="Operation erfolgreich.")
    id: Optional[int] = Field(default=None)
