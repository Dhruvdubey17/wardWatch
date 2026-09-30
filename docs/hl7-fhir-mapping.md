# HL7 v2 to FHIR R4 mapping

The FHIR service consumes `hl7.validated`, converts each message into FHIR R4 resources, stores them in Postgres and publishes every stored Observation to `fhir.observations`. The conversion code is `python/fhir_service/src/wardwatch_fhir/converter.py`, the storage rules are in `repository.py`, and the consumer is `validated_consumer.py`.

The converter reads the decoded segment arrays in the `hl7.validated` payload (`contracts/schemas/hl7.validated.schema.json`), not raw HL7. Fields are numbered as in the standard: `fields[0]` is field 1. Where a field has repetitions, components or subcomponents, the converter takes the first repetition and the first subcomponent of the component it needs, unless a table below says otherwise.

## Message types

The `hl7.validated` schema allows message types `ADT` and `ORU` and trigger events `A01`, `A03` and `R01`, so ingest never publishes anything else to this topic.

| Message | Resources produced |
|---|---|
| ADT^A01 (admit) | Patient, Encounter with status `in-progress` |
| ADT^A03 (discharge) | Patient, Encounter with status `finished` |
| ORU^R01 (results) | Patient, Encounter with status `in-progress`, and one Observation per convertible OBX |

Every message must carry PID and PV1. OBX segments are converted only when the message type is `ORU`.

## Identifiers and resource ids

| Identifier | Source | FHIR system |
|---|---|---|
| MRN | the payload's `mrn` field (the PID-3 identifier with type code MR, extracted by ingest) | `https://wardwatch.local/fhir/sid/mrn` |
| Visit number | PV1-19, component 1 | `https://wardwatch.local/fhir/sid/visit-number` |

Resource ids are derived from these identifiers so the same message always produces the same ids:

| Resource | `id` |
|---|---|
| Patient | the MRN |
| Encounter | the visit number (PV1-19) |
| Observation | `<MSH-10>-<OBX-1>`, the payload's `control_id` and the OBX set ID joined with a hyphen |

Every id passes through `fhir_id`: any character outside `A-Z`, `a-z`, `0-9`, `-` and `.` becomes `-`, and the result is cut to 64 characters, which is the FHIR id limit. For example `A B/C` becomes `A-B-C`. An identifier that sanitizes to an empty string raises `FHIR_ID_EMPTY`.

## Patient

| HL7 field | FHIR element | Notes |
|---|---|---|
| payload `mrn` | `id` | through `fhir_id` |
| payload `mrn` | `identifier[0]` | `use: usual`, `type.coding` is `MR` in `http://terminology.hl7.org/CodeSystem/v2-0203`, `system` is the MRN system, `value` is the MRN unchanged |
| PID-5.1 | `name[0].family` | `name[0].use` is `official`; `family` is always present, possibly empty |
| PID-5.2, PID-5.3 | `name[0].given` | given name then middle name or initial, empty parts dropped; `given` is omitted when both are empty |
| PID-7 | `birthDate` | the first 8 characters of the DTM, at their own precision (`1958`, `1958-02` or `1958-02-14`); omitted when PID-7 is empty |
| PID-8 | `gender` | HL7 table 0001, see below |
| PID-11.1 | `address[0].line[0]` | street |
| PID-11.3 | `address[0].city` | |
| PID-11.4 | `address[0].state` | |
| PID-11.5 | `address[0].postalCode` | |
| PID-11.6 | `address[0].country` | |

The address is emitted, with `use: home`, only when PID-11 has a street or a city. Each address element is omitted when its component is empty.

PID-8 uses HL7 table 0001 mapped to FHIR AdministrativeGender:

| PID-8 | `gender` |
|---|---|
| F | female |
| M | male |
| O | other |
| A | other |
| U | unknown |
| N | unknown |
| empty or any other value | unknown |

## Encounter

| HL7 field | FHIR element | Notes |
|---|---|---|
| PV1-19.1 | `id` | through `fhir_id`; an empty PV1-19 raises `VISIT_NUMBER_MISSING` |
| PV1-19.1 | `identifier[0]` | visit-number system, value unchanged |
| trigger event | `status` | `finished` for A03, `in-progress` for A01 and R01 |
| (fixed) | `class` | code `IMP`, display `inpatient encounter`, system `http://terminology.hl7.org/CodeSystem/v3-ActCode` |
| payload `mrn` | `subject.reference` | `Patient/<patient id>` |
| PV1-44 | `period.start` | admit date/time as a FHIR dateTime; omitted when empty |
| PV1-45 | `period.end` | discharge date/time as a FHIR dateTime; omitted when empty |
| PV1-3.1, PV1-3.2, PV1-3.3 | `location[0].location.display` | point of care, room and bed joined with single spaces, empty parts dropped (`ICU^01^A` becomes `ICU 01 A`); omitted when all three are empty |

`period` is omitted when both PV1-44 and PV1-45 are empty. The committed ORU^R01 sample leaves PV1-44 empty, so its Encounter has no `period`.

## Observation

An OBX becomes an Observation only when all of these hold:

- OBX-3.3 is `LN` and OBX-3.1 is a code in `contracts/loinc_codes.json`.
- OBX-2 is `NM`.
- OBX-5 is not empty.

Any other OBX was accepted by ingest (an unknown code is a warning there, not an error) but is not converted. The converter counts it in `skipped_observations`, and the consumer logs that count with the stored message. Skipped OBX segments are not stored.

| HL7 field | FHIR element | Notes |
|---|---|---|
| MSH-10, OBX-1 | `id` | `<control_id>-<set ID>` through `fhir_id` |
| OBX-11 | `status` | HL7 table 0085, see below |
| LOINC table `category` | `category[0].coding[0]` | system `http://terminology.hl7.org/CodeSystem/observation-category`, code `vital-signs` (display `Vital Signs`) or `laboratory` (display `Laboratory`) |
| OBX-3.1 | `code.coding[0].code` | system `http://loinc.org`; `display` comes from the LOINC table, not from the message |
| OBX-3.2 | `code.text` | falls back to the LOINC table display when OBX-3.2 is empty |
| payload `mrn` | `subject.reference` | `Patient/<patient id>` |
| PV1-19 | `encounter.reference` | `Encounter/<encounter id>` |
| OBX-14, else OBR-7 | `effectiveDateTime` | OBX-14 is often empty in feeds from bedside monitors, so the first OBR's OBR-7 is the fallback; when both are empty the message raises `OBSERVATION_TIME_MISSING` |
| OBX-5 | `valueQuantity.value` | parsed as a float, so `112` becomes `112.0` |
| OBX-6.1, else the LOINC table `ucum_unit` | `valueQuantity.unit` and `valueQuantity.code` | the same string in both; `valueQuantity.system` is `http://unitsofmeasure.org` |

OBX-11 uses HL7 table 0085, following the HL7 v2-to-FHIR ConceptMap:

| OBX-11 | `status` |
|---|---|
| F | final |
| U | final |
| C | corrected |
| A | amended |
| P | preliminary |
| R | preliminary |
| S | preliminary |
| I | registered |
| O | registered |
| X | cancelled |
| N | cancelled |
| D | entered-in-error |
| W | entered-in-error |

Any other OBX-11 value, including an empty one, raises `RESULT_STATUS_UNMAPPED` for the whole message.

### LOINC table

The converter uses `code`, `display`, `ucum_unit` and `category` from `contracts/loinc_codes.json`. The plausible ranges are listed for reference: ingest uses them to raise warnings, and the converter does not check them.

| LOINC | Display | PhysioNet variable | UCUM unit | Category | Plausible range |
|---|---|---|---|---|---|
| 8867-4 | Heart rate | HR | `/min` | vital-signs | 0 to 300 |
| 59408-5 | Oxygen saturation in Arterial blood by Pulse oximetry | O2Sat | `%` | vital-signs | 0 to 100 |
| 8310-5 | Body temperature | Temp | `Cel` | vital-signs | 25 to 45 |
| 8480-6 | Systolic blood pressure | SBP | `mm[Hg]` | vital-signs | 0 to 300 |
| 8478-0 | Mean blood pressure | MAP | `mm[Hg]` | vital-signs | 0 to 300 |
| 8462-4 | Diastolic blood pressure | DBP | `mm[Hg]` | vital-signs | 0 to 250 |
| 9279-1 | Respiratory rate | Resp | `/min` | vital-signs | 0 to 100 |
| 3150-0 | Inhaled oxygen concentration | FiO2 | `1` | vital-signs | 0.15 to 1.0 |
| 2524-7 | Lactate [Moles/volume] in Serum or Plasma | Lactate | `mmol/L` | laboratory | 0 to 40 |
| 6690-2 | Leukocytes [#/volume] in Blood by Automated count | WBC | `10*3/uL` | laboratory | 0 to 500 |
| 2160-0 | Creatinine [Mass/volume] in Serum or Plasma | Creatinine | `mg/dL` | laboratory | 0 to 30 |
| 777-3 | Platelets [#/volume] in Blood by Automated count | Platelets | `10*3/uL` | laboratory | 0 to 2000 |
| 1975-2 | Bilirubin.total [Mass/volume] in Serum or Plasma | Bilirubin_total | `mg/dL` | laboratory | 0 to 80 |

FiO2 travels as a fraction with UCUM unit `1`, matching PhysioNet, so `0.4` stays `0.4` with no scaling.

## Dates and times

HL7 DTM values (PID-7, PV1-44, PV1-45, OBX-14, OBR-7) must match `YYYY[MM[DD[HH[MM[SS[.S[S[S[S]]]]]]]]][+/-ZZZZ]`. Anything else, such as an ISO 8601 string like `2024-03-15`, raises `TIMESTAMP_INVALID`.

| DTM | FHIR |
|---|---|
| `2024` | `2024` |
| `202403` | `2024-03` |
| `20240315` | `2024-03-15` |
| `2024031513` | `2024-03-15T13:00:00+00:00` |
| `202403151307` | `2024-03-15T13:07:00+00:00` |
| `20240315130705` | `2024-03-15T13:07:05+00:00` |
| `20240315130705.25` | `2024-03-15T13:07:05.250000+00:00` |
| `20240315130705-0500` | `2024-03-15T13:07:05-05:00` |
| `20240315130705+0530` | `2024-03-15T13:07:05+05:30` |

A DTM with no time part keeps its own precision as a FHIR date. Once a time is present FHIR requires seconds and a timezone, so missing minutes and seconds are filled with zeros and a missing offset is read as UTC. The offset is kept as given, not converted to UTC. For `birthDate` only the first 8 characters are used, so any time part is dropped (`19580214083000` becomes `1958-02-14`).

## Skipped versus dead-lettered

A skipped OBX does not stop the message: the rest of it is converted and stored. A conversion error stops the whole message. The consumer publishes it to `hl7.deadletter` with `stage` set to `fhir`, the error code and detail, the MSH-10 as `control_id`, and the original HL7 bytes (decoded from the payload's `raw_base64`) in `raw_base64`. Nothing from that message is stored.

| Error code | Cause |
|---|---|
| `PID_MISSING` | no PID segment |
| `PV1_MISSING` | no PV1 segment |
| `VISIT_NUMBER_MISSING` | PV1-19 is empty |
| `FHIR_ID_EMPTY` | an identifier gives an empty FHIR id |
| `TIMESTAMP_INVALID` | PID-7, PV1-44, PV1-45, OBX-14 or OBR-7 is not a DTM |
| `RESULT_STATUS_UNMAPPED` | OBX-11 is not in table 0085 above |
| `OBSERVATION_TIME_MISSING` | a convertible OBX has neither OBX-14 nor OBR-7 |
| `PAYLOAD_INVALID` | the Kafka record is not JSON, lacks a required key, or fails with a `KeyError`, `TypeError` or `ValueError` during conversion (for example an OBX-5 that `float` cannot parse) |

For `PAYLOAD_INVALID` the dead-letter record has a null `control_id` and carries the Kafka record bytes, since the payload could not be read. The same fallback applies when `raw_base64` is not valid base64. The dead-letter record is keyed by the payload's MRN when there is one, else by an empty key.

## Idempotency

Kafka delivers at least once, and the consumer commits the offset only after the database transaction commits and every Observation is published. A replayed message therefore reaches the converter again. Because resource ids are derived from the message, the replay targets the same rows, and each upsert updates a row only when the stored resource JSON differs from the new one. A replay of an unchanged message leaves the database as it was.

| Resource | ADT^A01 | ADT^A03 | ORU^R01 |
|---|---|---|---|
| Patient | insert, or update if the resource changed | insert, or update if the resource changed | insert if missing, never update |
| Encounter | insert, or update if the resource changed | insert, or update if the resource changed | insert if missing, never update |
| Observation | not produced | not produced | insert, or update if the resource changed |

ADT messages own demographics and encounter state. A result message creates a Patient or Encounter that has not been seen yet but never overwrites one, so an ORU^R01 that arrives after a discharge does not move a `finished` Encounter back to `in-progress`.

Each stored row keeps the full resource JSON plus columns for querying. Patient rows keep the MRN, family name, given names joined with spaces, gender, and the birth date when it is a full `YYYY-MM-DD` date. Encounter rows keep the status, the location display as the bed, and the period start and end. Observation rows keep the patient and encounter ids, LOINC code, category, status, effective time, value, unit and the source message's MSH-7.

## The fhir.observations record

After the transaction commits, the consumer publishes each Observation from the message to `fhir.observations`, keyed by MRN, following `contracts/schemas/fhir.observations.schema.json`:

| Field | Value |
|---|---|
| `schema_version` | 1 |
| `mrn` | the payload's MRN |
| `encounter_id` | the Encounter id |
| `encounter_start` | the stored Encounter's `period.start` as ISO 8601, read back from the database after the write; null when the stored Encounter has no start |
| `message_time` | MSH-7 of the source message, from the payload, carried for end to end latency |
| `observation` | the full FHIR Observation resource |

`encounter_start` is read from the database rather than the message because an ORU^R01 usually has no PV1-44. The start comes from the admission that created the Encounter, and the scorer uses it to compute the ICU hour.

## Validation

The golden-file tests in `python/fhir_service/tests/unit/test_converter.py` validate every Patient, Encounter and Observation the converter emits with `fhir.resources.R4B`. Releases of `fhir.resources` from 7.0 on dropped the pure R4 module, and R4B keeps the R4 shape for every element the converter emits. The goldens are in `python/fhir_service/tests/fixtures/golden/`.

## Worked example

The committed sample `contracts/hl7/oru_r01.hl7` (payload `contracts/examples/hl7.validated/oru_r01.json`) contains these segments, among others:

```
MSH|^~\&|WWSIM|WARDWATCH_ICU|WARDWATCH|WARDWATCH|20240315130005+0000||ORU^R01^ORU_R01|SIM000007|P|2.5.1
PID|1||MRN0001234^^^WARDWATCH^MR||Lindgren^Ada^M||19580214|F
PV1|1|I|ICU^01^A^WARDWATCH||||||||||||||||ENC000001^^^WARDWATCH^VN
OBR|1||ENC000001-H0005^WWSIM|WW-HOURLY^Hourly ICU observations^L|||20240315130000+0000
OBX|1|NM|8867-4^Heart rate^LN||112|/min^/min^UCUM|||||F|||20240315130000+0000
```

All 13 OBX segments in the sample are numeric with codes in the LOINC table, so the message gives 13 Observations (`SIM000007-1` to `SIM000007-13`) and `skipped_observations` is 0. The first OBX becomes this Observation, taken from `python/fhir_service/tests/fixtures/golden/oru_r01.fhir.json`:

```json
{
  "resourceType": "Observation",
  "id": "SIM000007-1",
  "status": "final",
  "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs", "display": "Vital Signs"}]}],
  "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}], "text": "Heart rate"},
  "subject": {"reference": "Patient/MRN0001234"},
  "encounter": {"reference": "Encounter/ENC000001"},
  "effectiveDateTime": "2024-03-15T13:00:00+00:00",
  "valueQuantity": {"value": 112.0, "unit": "/min", "system": "http://unitsofmeasure.org", "code": "/min"}
}
```

The same message gives this Encounter. It has no `period` because PV1-44 is empty in a result message:

```json
{
  "resourceType": "Encounter",
  "id": "ENC000001",
  "identifier": [{"system": "https://wardwatch.local/fhir/sid/visit-number", "value": "ENC000001"}],
  "status": "in-progress",
  "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"},
  "subject": {"reference": "Patient/MRN0001234"},
  "location": [{"location": {"display": "ICU 01 A"}}]
}
```

If the ADT^A01 for this stay (`contracts/hl7/adt_a01.hl7`) was stored first, the Encounter row already exists with `period.start` of `2024-03-15T08:30:00+00:00`. The ORU^R01 does not overwrite it, and each `fhir.observations` record for this message carries that start as `encounter_start`.
