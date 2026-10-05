"""
Generate deterministic mock data for the SurgiCAL API.

All hospitals, providers, manufacturers and devices are fictional. CPT codes are
real code numbers with paraphrased descriptions; prices and quality scores are
synthetic but shaped like CMS / hospital price-transparency data.

Outputs (relative to this file):
    data/<table>.json   one file per table, backend-agnostic fixtures
    seed.sql            INSERTs for Postgres (run after migrations/*.sql)

Usage:
    python api/seed/generate.py
"""

from __future__ import annotations

import json
import math
import random
import uuid
from datetime import date, timedelta
from pathlib import Path

SEED = 42
OUT_DIR = Path(__file__).parent
DATA_DIR = OUT_DIR / "data"
UUID_NS = uuid.UUID("6f1c2a52-6a8e-4c1e-9b7a-5e3c0d2f8a11")

rng = random.Random(SEED)


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(UUID_NS, "|".join(parts)))


def money(x: float) -> float:
    return round(x, 2)


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# CPT codes: (code, description, category, body_system, avg_work_rvu, is_surgical, base_price)
# base_price = typical commercial facility rate, used to derive prices.
# ---------------------------------------------------------------------------
CPT_CODES = [
    ("27447", "Total knee replacement (arthroplasty)", "surgery", "musculoskeletal", 19.60, True, 32000),
    ("27130", "Total hip replacement (arthroplasty)", "surgery", "musculoskeletal", 19.60, True, 31000),
    ("23472", "Total shoulder replacement (arthroplasty)", "surgery", "musculoskeletal", 22.13, True, 34000),
    ("29881", "Knee arthroscopy with partial meniscus removal", "surgery", "musculoskeletal", 7.03, True, 6500),
    ("63030", "Lumbar laminotomy with disc removal, single level", "surgery", "nervous", 11.08, True, 19000),
    ("22551", "Anterior cervical discectomy and fusion, single level", "surgery", "musculoskeletal", 25.00, True, 42000),
    ("47562", "Laparoscopic gallbladder removal (cholecystectomy)", "surgery", "digestive", 10.47, True, 12500),
    ("49650", "Laparoscopic inguinal hernia repair", "surgery", "digestive", 6.27, True, 9500),
    ("44970", "Laparoscopic appendix removal (appendectomy)", "surgery", "digestive", 9.45, True, 11500),
    ("43644", "Laparoscopic gastric bypass (Roux-en-Y)", "surgery", "digestive", 29.40, True, 29000),
    ("33533", "Coronary artery bypass graft, single arterial graft", "surgery", "cardiovascular", 33.75, True, 68000),
    ("92928", "Coronary artery stent placement, single vessel", "surgery", "cardiovascular", 10.96, True, 19000),
    ("33208", "Dual-chamber permanent pacemaker insertion", "device", "cardiovascular", 7.80, True, 23000),
    ("66984", "Cataract removal with intraocular lens implant", "surgery", "eye", 8.52, True, 4200),
    ("58571", "Laparoscopic hysterectomy", "surgery", "female_genital", 15.10, True, 15500),
    ("19303", "Simple complete mastectomy", "surgery", "integumentary", 15.73, True, 14500),
    ("15823", "Upper eyelid lift (blepharoplasty)", "plastics", "integumentary", 6.81, True, 4800),
    ("45378", "Diagnostic colonoscopy", "surgery", "digestive", 3.26, False, 2600),
    ("70553", "MRI of the brain without and with contrast", "scans", "radiology", 2.29, False, 1900),
    ("73721", "MRI of a lower-extremity joint without contrast", "scans", "radiology", 1.35, False, 950),
    ("96365", "IV infusion, initial hour", "iv", "medicine", 0.21, False, 380),
]

TIER_CPTS = {
    "small": ["29881", "47562", "49650", "44970", "45378", "70553", "73721", "96365", "66984"],
    "medium": ["27447", "27130", "23472", "63030", "58571", "19303", "15823", "92928"],
    "large": ["33533", "33208", "22551", "43644"],
}

# ---------------------------------------------------------------------------
# Hospitals: (name, address, city, state, zip, lat, lng, tier, type, ownership)
# California only, matching production coverage.
# ---------------------------------------------------------------------------
HOSPITALS = [
    ("Bayshore Regional Medical Center", "1200 Embarcadero Way", "San Francisco", "CA", "94107", 37.7765, -122.3920, "large", "acute_care", "voluntary_nonprofit"),
    ("Golden Gate Heights Hospital", "455 Lawton St", "San Francisco", "CA", "94122", 37.7570, -122.4690, "medium", "acute_care", "proprietary"),
    ("Lake Merritt Medical Center", "300 Grand Ave", "Oakland", "CA", "94610", 37.8100, -122.2580, "medium", "acute_care", "voluntary_nonprofit"),
    ("Diablo Valley Regional Hospital", "2100 Ygnacio Valley Rd", "Walnut Creek", "CA", "94598", 37.9150, -122.0400, "medium", "acute_care", "voluntary_nonprofit"),
    ("Peninsula Oaks Medical Center", "800 Welch Rd", "Palo Alto", "CA", "94304", 37.4360, -122.1700, "large", "acute_care", "voluntary_nonprofit"),
    ("Coyote Creek Medical Center", "751 Bascom Ave", "San Jose", "CA", "95128", 37.3150, -121.9330, "large", "acute_care", "government"),
    ("Capitol River Medical Center", "2315 Stockton Blvd", "Sacramento", "CA", "95817", 38.5540, -121.4560, "large", "acute_care", "voluntary_nonprofit"),
    ("Foothill Ridge Hospital", "1 Medical Plaza Dr", "Roseville", "CA", "95661", 38.7580, -121.2560, "medium", "acute_care", "proprietary"),
    ("San Joaquin Plains Medical Center", "2823 Fresno St", "Fresno", "CA", "93721", 36.7480, -119.7820, "large", "acute_care", "government"),
    ("Kern Basin Community Hospital", "420 34th St", "Bakersfield", "CA", "93301", 35.3880, -119.0120, "medium", "acute_care", "proprietary"),
    ("Mesa Verde Coastal Hospital", "400 W Pueblo St", "Santa Barbara", "CA", "93105", 34.4300, -119.7200, "medium", "acute_care", "voluntary_nonprofit"),
    ("Angel City Medical Center", "1200 N State St", "Los Angeles", "CA", "90033", 34.0590, -118.2090, "large", "acute_care", "government"),
    ("Silver Lake Surgical Hospital", "2850 Sunset Blvd", "Los Angeles", "CA", "90026", 34.0860, -118.2730, "medium", "acute_care", "proprietary"),
    ("Arroyo Seco Memorial Hospital", "100 W California Blvd", "Pasadena", "CA", "91105", 34.1360, -118.1520, "large", "acute_care", "voluntary_nonprofit"),
    ("Harbor Breeze Medical Center", "2801 Atlantic Ave", "Long Beach", "CA", "90806", 33.8080, -118.1860, "medium", "acute_care", "voluntary_nonprofit"),
    ("Ocean Park Community Hospital", "1250 16th St", "Santa Monica", "CA", "90404", 34.0280, -118.4860, "medium", "acute_care", "voluntary_nonprofit"),
    ("South Bay Pacific Hospital", "3330 Lomita Blvd", "Torrance", "CA", "90505", 33.8090, -118.3420, "medium", "acute_care", "proprietary"),
    ("Orange Grove Medical Center", "16200 Sand Canyon Ave", "Irvine", "CA", "92618", 33.6560, -117.7750, "large", "acute_care", "voluntary_nonprofit"),
    ("Canyon Hills Hospital", "1111 W La Palma Ave", "Anaheim", "CA", "92801", 33.8470, -117.9310, "medium", "acute_care", "proprietary"),
    ("Jurupa Valley Medical Center", "4445 Magnolia Ave", "Riverside", "CA", "92501", 33.9760, -117.3880, "medium", "acute_care", "government"),
    ("Cajon Pass Regional Hospital", "1805 Medical Center Dr", "San Bernardino", "CA", "92411", 34.1350, -117.3220, "medium", "acute_care", "voluntary_nonprofit"),
    ("Balboa Mesa Medical Center", "7901 Frost St", "San Diego", "CA", "92123", 32.7980, -117.1550, "large", "acute_care", "voluntary_nonprofit"),
    ("Torrey Bluffs Hospital", "9888 Genesee Ave", "La Jolla", "CA", "92037", 32.8840, -117.2250, "large", "acute_care", "voluntary_nonprofit"),
    ("Shasta Cascade Medical Center", "1100 Butte St", "Redding", "CA", "96001", 40.5850, -122.3960, "medium", "acute_care", "voluntary_nonprofit"),
    ("Redwood Coast Community Hospital", "2700 Dolbeer St", "Eureka", "CA", "95501", 40.7840, -124.1450, "small", "critical_access", "government"),
    ("High Sierra Community Hospital", "10121 Pine Ave", "Truckee", "CA", "96161", 39.3290, -120.1950, "small", "critical_access", "voluntary_nonprofit"),
    ("Tahoe Basin Hospital", "1111 Emerald Bay Rd", "South Lake Tahoe", "CA", "96150", 38.9200, -119.9990, "small", "critical_access", "voluntary_nonprofit"),
]

# Hospital that has no CMS quality row at all (exercises LEFT JOIN nulls).
NO_QUALITY_HOSPITAL = "High Sierra Community Hospital"

STATE_CCN_PREFIX = {"CA": "05"}
AREA_CODES = {
    "San Francisco": "415", "Oakland": "510", "Walnut Creek": "925", "Palo Alto": "650", "San Jose": "408",
    "Sacramento": "916", "Roseville": "916", "Fresno": "559", "Bakersfield": "661", "Santa Barbara": "805",
    "Los Angeles": "213", "Pasadena": "626", "Long Beach": "562", "Santa Monica": "310", "Torrance": "310",
    "Irvine": "949", "Anaheim": "714", "Riverside": "951", "San Bernardino": "909", "San Diego": "858",
    "La Jolla": "858", "Redding": "530", "Eureka": "707", "Truckee": "530", "South Lake Tahoe": "530",
}

# ---------------------------------------------------------------------------
# California places for city / county / ZIP search.
# Every hospital city is listed; a few extra cities have no hospital, to
# exercise "valid place, no results". Centroids are approximate.
# ---------------------------------------------------------------------------
CITY_COUNTY = {
    "San Francisco": "San Francisco", "Oakland": "Alameda", "Walnut Creek": "Contra Costa", "Palo Alto": "Santa Clara",
    "San Jose": "Santa Clara", "Sacramento": "Sacramento", "Roseville": "Placer", "Fresno": "Fresno",
    "Bakersfield": "Kern", "Santa Barbara": "Santa Barbara", "Los Angeles": "Los Angeles", "Pasadena": "Los Angeles",
    "Long Beach": "Los Angeles", "Santa Monica": "Los Angeles", "Torrance": "Los Angeles", "Irvine": "Orange",
    "Anaheim": "Orange", "Riverside": "Riverside", "San Bernardino": "San Bernardino", "San Diego": "San Diego",
    "La Jolla": "San Diego", "Redding": "Shasta", "Eureka": "Humboldt", "Truckee": "Nevada",
    "South Lake Tahoe": "El Dorado",
}
EXTRA_CITIES = [
    # (city, county, lat, lng)
    ("Berkeley", "Alameda", 37.8715, -122.2730),
    ("Glendale", "Los Angeles", 34.1425, -118.2551),
    ("Burbank", "Los Angeles", 34.1808, -118.3090),
    ("Santa Ana", "Orange", 33.7455, -117.8677),
    ("Chula Vista", "San Diego", 32.6401, -117.0842),
    ("Modesto", "Stanislaus", 37.6391, -120.9969),
    ("Stockton", "San Joaquin", 37.9577, -121.2908),
    ("Santa Rosa", "Sonoma", 38.4404, -122.7141),
    ("Ventura", "Ventura", 34.2746, -119.2290),
    ("Salinas", "Monterey", 36.6777, -121.6555),
    ("San Luis Obispo", "San Luis Obispo", 35.2828, -120.6596),
    ("Chico", "Butte", 39.7285, -121.8375),
]
COUNTY_CENTROIDS = {
    "San Francisco": (37.7599, -122.4370), "Alameda": (37.6480, -121.9130), "Contra Costa": (37.9190, -121.9510),
    "Santa Clara": (37.2330, -121.6950), "Sacramento": (38.4500, -121.3400), "Placer": (39.0630, -120.7180),
    "Fresno": (36.7580, -119.6490), "Kern": (35.3430, -118.7290), "Santa Barbara": (34.6700, -120.0170),
    "Los Angeles": (34.3210, -118.2250), "Orange": (33.7030, -117.7610), "Riverside": (33.7430, -115.9940),
    "San Bernardino": (34.8410, -116.1780), "San Diego": (33.0340, -116.7360), "Shasta": (40.7630, -122.0410),
    "Humboldt": (40.7060, -123.9260), "Nevada": (39.3010, -120.7690), "El Dorado": (38.7790, -120.5250),
    "Stanislaus": (37.5590, -120.9980), "San Joaquin": (37.9350, -121.2720), "Sonoma": (38.5250, -122.9260),
    "Ventura": (34.4570, -119.0830), "Monterey": (36.2170, -121.2390), "San Luis Obispo": (35.3870, -120.4040),
    "Butte": (39.6670, -121.6010),
}

CMS_GROUPS = ["Below the national average", "Same as the national average", "Above the national average"]

# ---------------------------------------------------------------------------
# Payers
# ---------------------------------------------------------------------------
COMMERCIAL_PAYERS = {
    "CA": [("Aetna", 1.00), ("Anthem Blue Cross", 1.05), ("Blue Shield of California", 1.10), ("Cigna", 0.98), ("UnitedHealthcare", 1.08)],
}
MEDICARE = ("Medicare", 0.42)

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
SPECIALTIES = [
    # (specialty, taxonomy_code, weight, min_tier)
    ("Orthopaedic Surgery", "207X00000X", 22, "medium"),
    ("General Surgery", "208600000X", 18, "small"),
    ("Neurological Surgery", "207T00000X", 8, "medium"),
    ("Thoracic Surgery (Cardiothoracic Vascular Surgery)", "208G00000X", 5, "large"),
    ("Interventional Cardiology", "207RI0011X", 8, "medium"),
    ("Ophthalmology", "207W00000X", 8, "small"),
    ("Obstetrics & Gynecology", "207V00000X", 8, "medium"),
    ("Gastroenterology", "207RG0100X", 7, "small"),
    ("Diagnostic Radiology", "2085R0202X", 6, "small"),
    ("Plastic Surgery", "208200000X", 5, "medium"),
    ("Anesthesiology", "207L00000X", 5, "small"),
]
TIER_RANK = {"small": 0, "medium": 1, "large": 2}

FIRST_NAMES_F = ["Amelia", "Priya", "Sofia", "Grace", "Hannah", "Mei", "Isabel", "Nora", "Leah", "Camila",
                 "Ava", "Zara", "Elena", "Maya", "Claire", "Aisha", "Julia", "Naomi", "Rosa", "Ingrid"]
FIRST_NAMES_M = ["James", "Arjun", "Mateo", "Daniel", "Wei", "Samuel", "Omar", "Lucas", "Ethan", "Kenji",
                 "Gabriel", "Henry", "Rafael", "David", "Nikhil", "Thomas", "Andre", "Felix", "Marcus", "Owen"]
LAST_NAMES = ["Alvarez", "Brennan", "Chen", "Dubois", "Eriksen", "Fujimoto", "Garcia", "Hartley", "Iyer", "Jensen",
              "Kowalski", "Lindqvist", "Moreno", "Nakamura", "Okafor", "Patel", "Quinn", "Ramirez", "Sato", "Thornton",
              "Underwood", "Vasquez", "Whitfield", "Xu", "Yamamoto", "Zimmerman", "Abernathy", "Castellanos",
              "Delgado", "Feld", "Gonzaga", "Hollis", "Kaur", "Larkin", "Mendoza", "Novak", "Osei", "Prescott",
              "Reyes", "Sorensen", "Tanaka", "Valdez", "Wexler", "Yoon"]
MEDICAL_SCHOOLS = ["UCSF School of Medicine", "Stanford University School of Medicine", "David Geffen School of Medicine at UCLA",
                   "Keck School of Medicine of USC", "UC San Diego School of Medicine", "UC Davis School of Medicine",
                   "University of Nevada, Reno School of Medicine", "Johns Hopkins University School of Medicine",
                   "University of Michigan Medical School", "Baylor College of Medicine", "Touro University College of Osteopathic Medicine",
                   "Western University of Health Sciences COMP"]

# ---------------------------------------------------------------------------
# Devices: (brand_name, generic_name, manufacturer, product_code, class, specialty, cpts, usage_type, description)
# ---------------------------------------------------------------------------
DEVICES = [
    ("Meridian Ascend Total Knee System", "Knee joint patellofemorotibial prosthesis, cemented", "Meridian Orthopedics", "JWH", "II", "Orthopedic", ["27447"], "implant",
     "Cemented posterior-stabilized total knee system with cobalt-chrome femoral component and highly cross-linked polyethylene insert."),
    ("Meridian Ascend Revision Knee", "Knee joint prosthesis, constrained, cemented", "Meridian Orthopedics", "KRO", "II", "Orthopedic", ["27447"], "implant",
     "Constrained condylar knee for revision cases with modular stems and augments."),
    ("Halcyon Kinetic Knee", "Knee joint patellofemorotibial prosthesis, uncemented", "Halcyon Biomedical", "MBH", "II", "Orthopedic", ["27447"], "implant",
     "Cementless cruciate-retaining knee with porous titanium tibial baseplate."),
    ("Meridian Stride Hip Stem", "Hip joint femoral stem prosthesis, uncemented", "Meridian Orthopedics", "LPH", "II", "Orthopedic", ["27130"], "implant",
     "Tapered titanium femoral stem with hydroxyapatite coating."),
    ("Halcyon Arcus Acetabular Cup", "Hip joint acetabular cup, porous coated", "Halcyon Biomedical", "LPH", "II", "Orthopedic", ["27130"], "implant",
     "Multi-hole acetabular shell compatible with ceramic and polyethylene liners."),
    ("Halcyon Arcus Ceramic Head", "Hip joint femoral head, ceramic", "Halcyon Biomedical", "LZO", "II", "Orthopedic", ["27130"], "implant",
     "Delta-ceramic femoral head in 28-40 mm diameters."),
    ("Meridian Summit Shoulder", "Shoulder joint prosthesis, anatomic", "Meridian Orthopedics", "KWS", "II", "Orthopedic", ["23472"], "implant",
     "Convertible anatomic/reverse total shoulder platform."),
    ("Bondline PMMA Bone Cement", "Bone cement, polymethylmethacrylate", "Halcyon Biomedical", "LOD", "II", "Orthopedic", ["27447", "27130", "23472"], "consumable",
     "Medium-viscosity PMMA cement with gentamicin."),
    ("Apex ArthroBlade Shaver System", "Arthroscope accessory, powered shaver", "Apex Surgical Instruments", "HRX", "II", "Orthopedic", ["29881"], "instrument",
     "Powered arthroscopic shaver with single-use blades."),
    ("Northstar Keel Pedicle Screw System", "Thoracolumbosacral pedicle screw system", "Northstar Spine", "NKB", "II", "Orthopedic", ["63030"], "implant",
     "Polyaxial pedicle screws and titanium rods for posterior lumbar stabilization."),
    ("Northstar Lattice Cervical Cage", "Intervertebral body fusion device, cervical", "Northstar Spine", "ODP", "II", "Orthopedic", ["22551"], "implant",
     "3D-printed porous titanium interbody cage for ACDF."),
    ("Northstar Lattice Cervical Plate", "Spinal intervertebral body fixation orthosis", "Northstar Spine", "KWQ", "II", "Orthopedic", ["22551"], "implant",
     "Low-profile anterior cervical plate with locking screws."),
    ("Apex EndoCut Laparoscopic Stapler", "Surgical stapler, laparoscopic", "Apex Surgical Instruments", "GDW", "II", "General & Plastic Surgery", ["47562", "43644", "44970"], "instrument",
     "Articulating 45/60 mm endoscopic linear cutter."),
    ("Apex PortSeal Trocar", "Laparoscope accessory, trocar", "Apex Surgical Instruments", "GCJ", "II", "General & Plastic Surgery", ["47562", "49650", "44970", "43644", "58571"], "consumable",
     "Bladeless optical-entry trocar, 5-12 mm."),
    ("Halcyon FlexWeave Hernia Mesh", "Surgical mesh, polymeric", "Halcyon Biomedical", "FTL", "II", "General & Plastic Surgery", ["49650"], "implant",
     "Lightweight macroporous polypropylene mesh, pre-shaped for laparoscopic inguinal repair."),
    ("Halcyon FlexWeave Composite Mesh", "Surgical mesh, polymeric, coated", "Halcyon Biomedical", "FTL", "II", "General & Plastic Surgery", ["49650"], "implant",
     "Polypropylene mesh with absorbable anti-adhesion coating."),
    ("Cardiovance Lumina DES", "Coronary drug-eluting stent", "Cardiovance Medical", "NIQ", "III", "Cardiovascular", ["92928"], "implant",
     "Everolimus-eluting cobalt-chromium coronary stent."),
    ("Cardiovance Lumina Thin DES", "Coronary drug-eluting stent", "Cardiovance Medical", "NIQ", "III", "Cardiovascular", ["92928"], "implant",
     "Ultra-thin strut sirolimus-eluting stent with bioabsorbable polymer."),
    ("Cardiovance Pulse DR Pacemaker", "Implantable pacemaker pulse generator", "Cardiovance Medical", "DXY", "III", "Cardiovascular", ["33208"], "implant",
     "Dual-chamber MRI-conditional pacemaker with remote monitoring."),
    ("Cardiovance Pulse Pacing Lead", "Permanent pacemaker electrode", "Cardiovance Medical", "DTB", "III", "Cardiovascular", ["33208"], "implant",
     "Active-fixation bipolar pacing lead."),
    ("Apex HarvestPro Vein System", "Endoscopic vessel harvesting system", "Apex Surgical Instruments", "GCJ", "II", "Cardiovascular", ["33533"], "instrument",
     "Endoscopic saphenous vein and radial artery harvesting system."),
    ("Lumen Vision ClearView IOL", "Intraocular lens, posterior chamber", "Lumen Vision Inc.", "HQL", "III", "Ophthalmic", ["66984"], "implant",
     "Single-piece hydrophobic acrylic monofocal IOL."),
    ("Lumen Vision ClearView Toric IOL", "Intraocular lens, toric", "Lumen Vision Inc.", "MJP", "III", "Ophthalmic", ["66984"], "implant",
     "Astigmatism-correcting toric IOL."),
    ("Lumen Vision Phaco 9 System", "Phacoemulsification system", "Lumen Vision Inc.", "HQC", "II", "Ophthalmic", ["66984"], "instrument",
     "Torsional phacoemulsification console and handpiece."),
    ("Apex UteraGuide Manipulator", "Uterine manipulator", "Apex Surgical Instruments", "LKF", "II", "Obstetrics/Gynecology", ["58571"], "instrument",
     "Single-use uterine manipulator with colpotomy cup."),
    ("Keystone EndoView HD Colonoscope", "Colonoscope and accessories", "Keystone Endoscopy", "FDF", "II", "Gastroenterology/Urology", ["45378"], "instrument",
     "High-definition video colonoscope with water-jet channel."),
    ("Halcyon ContourForm Tissue Expander", "Breast tissue expander", "Halcyon Biomedical", "LCJ", "II", "General & Plastic Surgery", ["19303"], "implant",
     "Textured anatomical tissue expander with integrated port."),
    ("Keystone FlowSet IV Infusion Set", "Intravascular administration set", "Keystone Endoscopy", "FPA", "II", "General Hospital", ["96365"], "consumable",
     "Gravity IV administration set with needle-free connector."),
]

RECALL_REASONS = [
    "Polyethylene insert packaging seal may be compromised, potentially affecting sterility.",
    "Locking mechanism may not fully engage, which could lead to component disassociation.",
    "Labeling lists an incorrect size on the outer carton.",
    "Higher-than-expected rate of early loosening reported for a specific lot range.",
    "Firmware anomaly may cause premature battery depletion indicator.",
    "Delivery system balloon may fail to deflate fully.",
    "Instructions for use omit a contraindication for patients with nickel sensitivity.",
    "Stapler may misfire when articulated beyond 45 degrees.",
    "Lens haze reported after implantation for a limited number of lots.",
    "Seal on trocar cannula may leak insufflation gas.",
]
DEVICE_PROBLEMS = {
    "implant": ["Loosening of Implant Not Related to Bone-Ingrowth", "Fracture", "Migration", "Wear", "Device Dislodged or Dislocated", "Material Deformation"],
    "instrument": ["Misfire", "Mechanical Jam", "Device Operates Differently Than Expected", "Break", "Electrical Issue"],
    "consumable": ["Leak/Splash", "Packaging Problem", "Contamination", "Break"],
}
PATIENT_OUTCOMES = {
    "death": [["Death"]],
    "injury": [["Required Intervention"], ["Hospitalization", "Required Intervention"], ["Other"], ["Disability"]],
    "malfunction": [["No Consequences Or Impact To Patient"], ["No Known Impact Or Consequence To Patient"]],
}
NARRATIVES = {
    "death": "It was reported that the patient expired {d} days after the procedure. The relationship between the device and the death has not been established. Investigation is ongoing.",
    "injury": "It was reported that the patient required revision surgery approximately {d} days post-procedure due to {p}. The device was explanted and returned for evaluation.",
    "malfunction": "During the procedure the user observed {p}. A backup device was used and the procedure was completed without delay. No patient injury was reported.",
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def build_cpt_codes() -> list[dict]:
    return [
        {"code": c, "description": d, "category": cat, "body_system": bs, "avg_work_rvu": rvu, "is_surgical": surg}
        for c, d, cat, bs, rvu, surg, _ in CPT_CODES
    ]


def build_places(hospitals: list[dict]) -> list[dict]:
    places = []
    for county, (lat, lng) in sorted(COUNTY_CENTROIDS.items()):
        places.append({"place_type": "county", "name": county, "county": county, "lat": lat, "lng": lng})

    # City centroid = mean of its hospitals' coordinates (mock approximation).
    by_city: dict[str, list[dict]] = {}
    for h in hospitals:
        by_city.setdefault(h["city"], []).append(h)
    for city, hs in sorted(by_city.items()):
        lat = round(sum(h["lat"] for h in hs) / len(hs), 4)
        lng = round(sum(h["lng"] for h in hs) / len(hs), 4)
        places.append({"place_type": "city", "name": city, "county": CITY_COUNTY[city], "lat": lat, "lng": lng})
    for city, county, lat, lng in EXTRA_CITIES:
        places.append({"place_type": "city", "name": city, "county": county, "lat": lat, "lng": lng})

    # ZIP centroid = first hospital in that ZIP.
    seen: set[str] = set()
    for h in hospitals:
        if h["zip"] not in seen:
            seen.add(h["zip"])
            places.append({"place_type": "zip", "name": h["zip"], "county": CITY_COUNTY[h["city"]], "lat": h["lat"], "lng": h["lng"]})
    return places


def npi_check_digit(base9: str) -> str:
    """NPI check digit: Luhn over '80840' + first 9 digits."""
    digits = [int(x) for x in "80840" + base9]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def make_npi(used: set[str], first: str) -> str:
    while True:
        base9 = first + "".join(str(rng.randint(0, 9)) for _ in range(8))
        npi = base9 + npi_check_digit(base9)
        if npi not in used:
            used.add(npi)
            return npi


def build_hospitals(used_npis: set[str]) -> tuple[list[dict], list[dict]]:
    hospitals, quality = [], []
    counters: dict[tuple[str, bool], int] = {}
    for name, addr, city, state, zip_, lat, lng, tier, htype, own in HOSPITALS:
        # CMS ranges: 0001-0879 short-term acute, 1300-1399 critical access.
        key = (state, htype == "critical_access")
        counters[key] = counters.get(key, 0) + 1
        seq = (1380 if key[1] else 800) + counters[key]
        ccn = f"{STATE_CCN_PREFIX[state]}{seq:04d}"
        hospitals.append({
            "ccn": ccn,
            "npi": make_npi(used_npis, "1"),
            "name": name,
            "address": addr,
            "city": city,
            "state": state,
            "zip": zip_,
            "lat": lat,
            "lng": lng,
            "phone": f"({AREA_CODES[city]}) 555-{rng.randint(1000, 9999)}",
            "hospital_type": htype,
            "ownership": own,
            "emergency_services": htype != "critical_access" or rng.random() < 0.7,
            "_tier": tier,
        })
        if name == NO_QUALITY_HOSPITAL:
            continue
        stars = None if htype == "critical_access" and rng.random() < 0.5 else float(rng.choices([1, 2, 3, 4, 5], [1, 3, 5, 4, 2])[0])
        cardiac = tier == "large"
        ortho = tier != "small"
        quality.append({
            "ccn": ccn,
            "overall_stars": stars,
            "mortality_group": rng.choice(CMS_GROUPS),
            "safety_group": rng.choice(CMS_GROUPS),
            "readmission_group": rng.choice(CMS_GROUPS),
            "patient_experience_group": rng.choice(CMS_GROUPS),
            "timely_care_group": rng.choice(CMS_GROUPS),
            "psi90_composite": round(rng.uniform(0.80, 1.25), 2),
            "hai_sirs": {k: round(rng.uniform(0.3, 1.6), 3) for k in ("CLABSI", "CAUTI", "SSI_colon", "MRSA", "CDI")},
            "readmission_hip_knee": round(rng.uniform(3.4, 5.6), 1) if ortho else None,
            "complication_hip_knee": round(rng.uniform(1.8, 3.6), 1) if ortho else None,
            "mortality_cabg": round(rng.uniform(1.9, 4.2), 1) if cardiac else None,
            "readmission_cabg": round(rng.uniform(9.5, 14.5), 1) if cardiac else None,
            "readmission_hosp_wide": round(rng.uniform(13.5, 16.8), 1),
            "measure_period": "2025Q4",
        })
    return hospitals, quality


def build_prices(hospitals: list[dict]) -> list[dict]:
    base = {c[0]: c[6] for c in CPT_CODES}
    prices = []
    for h in hospitals:
        tier = h["_tier"]
        cpts = list(TIER_CPTS["small"])
        if TIER_RANK[tier] >= 1:
            cpts += TIER_CPTS["medium"]
        if tier == "large":
            cpts += TIER_CPTS["large"]
        # Drop a few at random, but keep knee replacement everywhere it's offered (main demo procedure).
        cpts = [c for c in cpts if c == "27447" or rng.random() < 0.9]

        hosp_mult = rng.uniform(0.65, 1.55) * (1.12 if h["city"] in ("San Francisco", "Palo Alto") else 1.0)
        payers = rng.sample(COMMERCIAL_PAYERS[h["state"]], 3)
        source = rng.choice(["mrf_parsed", "mrf_parsed", "oria_trilliant"])
        measure_date = date(2026, 1, 1) if source == "mrf_parsed" else date(2025, 10, 1)

        for cpt in cpts:
            b = base[cpt] * hosp_mult
            cash = money(b * rng.uniform(0.75, 0.95))
            rows = []
            for payer, pm in payers:
                plans = ["PPO"] + (["HMO"] if rng.random() < 0.4 else [])
                for plan in plans:
                    rate = b * pm * rng.uniform(0.9, 1.1) * (0.92 if plan == "HMO" else 1.0)
                    rows.append([payer, f"{payer} {plan}", money(rate)])
            rows.append([MEDICARE[0], "Traditional Medicare", money(b * MEDICARE[1] * rng.uniform(0.97, 1.03))])
            rows.append(["CASH", "", cash])
            rates = [r[2] for r in rows]
            lo, hi = min(rates), max(rates)
            for payer, plan, rate in rows:
                prices.append({
                    "cpt": cpt,
                    "ccn": h["ccn"],
                    "payer": payer,
                    "plan_name": plan,
                    "billing_class": "facility",
                    "cash_price": cash,
                    "negotiated_rate": rate,
                    "negotiated_min": lo,
                    "negotiated_max": hi,
                    "source": source,
                    "measure_date": measure_date.isoformat(),
                })
    return prices


def build_providers(hospitals: list[dict], used_npis: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    providers, affiliations, metrics = [], [], []
    used_names: set[tuple[str, str]] = set()
    weights = [s[2] for s in SPECIALTIES]
    hosp_weights = [{"small": 1, "medium": 3, "large": 5}[h["_tier"]] for h in hospitals]

    for _ in range(110):
        specialty, taxonomy, _, min_tier = rng.choices(SPECIALTIES, weights)[0]
        eligible = [(h, w) for h, w in zip(hospitals, hosp_weights, strict=True) if TIER_RANK[h["_tier"]] >= TIER_RANK[min_tier]]
        primary = rng.choices([h for h, _ in eligible], [w for _, w in eligible])[0]

        gender = rng.choice(["F", "M"])
        while True:
            first = rng.choice(FIRST_NAMES_F if gender == "F" else FIRST_NAMES_M)
            last = rng.choice(LAST_NAMES)
            if (first, last) not in used_names:
                used_names.add((first, last))
                break

        npi = make_npi(used_npis, "1")
        grad = rng.randint(1985, 2016)
        providers.append({
            "npi": npi,
            "first_name": first,
            "last_name": last,
            "credential": rng.choices(["MD", "DO"], [85, 15])[0],
            "specialty": specialty,
            "taxonomy_code": taxonomy,
            "gender": gender,
            "medical_school": rng.choice(MEDICAL_SCHOOLS),
            "graduation_year": grad,
            "city": primary["city"],
            "state": primary["state"],
            "lat": round(primary["lat"] + rng.uniform(-0.03, 0.03), 6),
            "lng": round(primary["lng"] + rng.uniform(-0.03, 0.03), 6),
        })

        affiliations.append({"npi": npi, "ccn": primary["ccn"], "is_primary": True})
        nearby = [
            h for h in eligible
            if h[0]["ccn"] != primary["ccn"] and haversine_miles((primary["lat"], primary["lng"]), (h[0]["lat"], h[0]["lng"])) < 40
        ]
        for h, _ in rng.sample(nearby, min(len(nearby), rng.choice([0, 1, 1, 2]))):
            affiliations.append({"npi": npi, "ccn": h["ccn"], "is_primary": False})

        # ~10% of providers have no metrics row (exercises LEFT JOIN nulls).
        if rng.random() < 0.10:
            continue
        services = int(rng.lognormvariate(6.3, 0.8))
        wrvu = round(services * rng.uniform(2.0, 9.0), 1)
        metrics.append({
            "npi": npi,
            "patient_rating": round(rng.uniform(3.2, 5.0), 1),
            "num_reviews": rng.randint(3, 420),
            "total_medicare_services": services,
            "total_medicare_beneficiaries": int(services * rng.uniform(0.4, 0.8)),
            "total_medicare_payment": money(services * rng.uniform(80, 900)),
            "wrvu_estimate": wrvu,
            "volume_bucket": "HIGH" if wrvu > 4000 else "MEDIUM" if wrvu > 1500 else "LOW",
            "trilliant_specialty": specialty,
            "trilliant_active": rng.random() < 0.95,
            "patient_demographics": {
                "avg_age": rng.randint(48, 76),
                "pct_female": round(rng.uniform(0.35, 0.65), 2),
                "pct_medicare": round(rng.uniform(0.25, 0.70), 2),
                "pct_dual_eligible": round(rng.uniform(0.05, 0.30), 2),
            },
            "has_sanctions": rng.random() < 0.03,
            "specialty_classification": "Physician",
        })
    return providers, affiliations, metrics


def build_devices() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    devices, proc_map, recalls, events = [], [], [], []
    used_premarket: set[str] = set()
    recall_seq = 100
    mdr_key = 18_400_000

    for brand, generic, mfr, code, cls, spec, cpts, usage, desc in DEVICES:
        device_id = stable_uuid("device", brand)
        while True:
            pm = ("P" if cls == "III" else "K") + f"{rng.randint(10, 25):02d}{rng.randint(0, 9999):04d}"
            if pm not in used_premarket:
                used_premarket.add(pm)
                break
        devices.append({
            "id": device_id,
            "fda_product_code": code,
            "brand_name": brand,
            "generic_name": generic,
            "manufacturer": mfr,
            "device_class": cls,
            "medical_specialty": spec,
            "premarket_number": pm,
            "description": desc,
        })
        for cpt in cpts:
            proc_map.append({"device_id": device_id, "cpt": cpt, "usage_type": usage})

        # Recalls: ~40% of devices, more likely for class III.
        n_recalls = rng.choices([0, 1, 2], [55, 35, 10] if cls == "II" else [35, 45, 20])[0]
        for _ in range(n_recalls):
            recall_seq += rng.randint(7, 60)
            rdate = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1900))
            terminated = rdate < date(2024, 6, 1) and rng.random() < 0.7
            recalls.append({
                "device_id": device_id,
                "recall_number": f"Z-{recall_seq:04d}-{rdate.year}",
                "product_code": code,
                "brand_name": brand,
                "manufacturer": mfr,
                "recall_class": rng.choices(["Class I", "Class II", "Class III"], [10, 75, 15])[0],
                "reason": rng.choice(RECALL_REASONS),
                "status": "Terminated" if terminated else rng.choice(["Open, Classified", "Completed"]),
                "recall_date": rdate.isoformat(),
                "termination_date": (rdate + timedelta(days=rng.randint(120, 700))).isoformat() if terminated else None,
                "quantity": f"{rng.randint(40, 25000):,} units",
                "distribution": rng.choice(["Nationwide (US)", "US: CA, NV, AZ, OR, WA", "Worldwide", "US Nationwide and Canada"]),
            })

        # Adverse events: class III devices and implants get more reports.
        n_events = rng.randint(0, 3) + (4 if cls == "III" else 0) + (2 if usage == "implant" else 0)
        for _ in range(n_events):
            mdr_key += rng.randint(50, 9000)
            etype = rng.choices(["malfunction", "injury", "death"], [60, 37, 3])[0]
            problems = rng.sample(DEVICE_PROBLEMS[usage], rng.randint(1, 2))
            events.append({
                "device_id": device_id,
                "mdr_report_key": str(mdr_key),
                "product_code": code,
                "brand_name": brand,
                "manufacturer": mfr,
                "event_type": etype,
                "event_date": (date(2022, 1, 1) + timedelta(days=rng.randint(0, 1550))).isoformat(),
                "patient_outcomes": rng.choice(PATIENT_OUTCOMES[etype]),
                "device_problems": problems,
                "event_narrative": NARRATIVES[etype].format(d=rng.randint(1, 400), p=problems[0].lower()),
            })
    return devices, proc_map, recalls, events


# ---------------------------------------------------------------------------
# SQL output
# ---------------------------------------------------------------------------
GEO_TABLES = {"hospitals", "providers", "ca_places"}
JSON_COLUMNS = {"hai_sirs", "patient_demographics", "patient_outcomes", "device_problems"}


def sql_literal(col: str, v) -> str:
    if v is None:
        return "NULL"
    if col in JSON_COLUMNS:
        return "'" + json.dumps(v).replace("'", "''") + "'::jsonb"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def sql_insert(table: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    cols = [c for c in rows[0] if c not in ("lat", "lng")]
    out_cols = cols + (["location"] if table in GEO_TABLES else [])
    values = []
    for r in rows:
        vals = [sql_literal(c, r[c]) for c in cols]
        if table in GEO_TABLES:
            vals.append(f"ST_SetSRID(ST_MakePoint({r['lng']}, {r['lat']}), 4326)::geography")
        values.append("  (" + ", ".join(vals) + ")")
    return f"INSERT INTO {table} ({', '.join(out_cols)}) VALUES\n" + ",\n".join(values) + ";\n"


def main() -> None:
    used_npis: set[str] = set()
    hospitals, quality = build_hospitals(used_npis)
    providers, affiliations, metrics = build_providers(hospitals, used_npis)
    prices = build_prices(hospitals)
    devices, proc_map, recalls, events = build_devices()
    for h in hospitals:
        del h["_tier"]

    # Insertion order respects foreign keys.
    tables = {
        "ca_places": build_places(hospitals),
        "cpt_codes": build_cpt_codes(),
        "hospitals": hospitals,
        "hospital_quality": quality,
        "providers": providers,
        "provider_affiliations": affiliations,
        "provider_metrics": metrics,
        "prices": prices,
        "devices": devices,
        "device_procedure_map": proc_map,
        "device_recalls": recalls,
        "device_adverse_events": events,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        (DATA_DIR / f"{name}.json").write_text(json.dumps(rows, indent=2) + "\n")

    sql = [
        "-- Generated by api/seed/generate.py. Do not edit by hand.",
        "-- Fictional mock data. Apply after migrations/*.sql.",
        "BEGIN;",
        "TRUNCATE " + ", ".join(reversed(tables)) + " CASCADE;",
    ]
    sql += [sql_insert(name, rows) for name, rows in tables.items()]
    sql.append("COMMIT;")
    (OUT_DIR / "seed.sql").write_text("\n".join(sql) + "\n")

    for name, rows in tables.items():
        print(f"{name:24s} {len(rows):5d} rows")


if __name__ == "__main__":
    main()
