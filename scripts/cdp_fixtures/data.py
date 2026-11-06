"""Synthetic CDP-onboarding reference data and 20 onboarding requests (ADR 0010).

Everything is invented: the data practice (Meridian & Pike Data), clients, people, and every value
in the sample extracts. Unmasked identifiers use reserved example domains and 555 numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

D = date.fromisoformat
PRACTICE = "Meridian & Pike Data"


@dataclass(frozen=True)
class Attribute:
    name: str
    entity: str
    data_type: str
    personal_class: str
    identity_key: bool


ATTRIBUTES = {
    a.name: a
    for a in (
        Attribute("customer_id", "PROFILE", "STRING", "NONE", True),
        Attribute("email_address", "PROFILE", "EMAIL", "DIRECT_IDENTIFIER", True),
        Attribute("phone_number", "PROFILE", "PHONE", "DIRECT_IDENTIFIER", True),
        Attribute("first_name", "PROFILE", "STRING", "DIRECT_IDENTIFIER", False),
        Attribute("last_name", "PROFILE", "STRING", "DIRECT_IDENTIFIER", False),
        Attribute("postal_code", "PROFILE", "STRING", "QUASI_IDENTIFIER", False),
        Attribute("birth_date", "PROFILE", "DATE", "QUASI_IDENTIFIER", False),
        Attribute("loyalty_id", "PROFILE", "STRING", "QUASI_IDENTIFIER", True),
        Attribute("device_id", "PROFILE", "STRING", "QUASI_IDENTIFIER", True),
        Attribute("loyalty_tier", "PROFILE", "STRING", "NONE", False),
        Attribute("marketing_opt_in", "PROFILE", "BOOLEAN", "NONE", False),
        Attribute("account_created_at", "PROFILE", "TIMESTAMP", "NONE", False),
        Attribute("country_code", "PROFILE", "STRING", "NONE", False),
        Attribute("health_condition", "PROFILE", "STRING", "SENSITIVE", False),
        Attribute("accessibility_needs", "PROFILE", "STRING", "SENSITIVE", False),
        Attribute("account_balance_band", "PROFILE", "STRING", "NONE", False),
        Attribute("order_id", "EVENT", "STRING", "NONE", False),
        Attribute("order_total", "EVENT", "NUMBER", "NONE", False),
        Attribute("order_timestamp", "EVENT", "TIMESTAMP", "NONE", False),
        Attribute("product_sku", "EVENT", "STRING", "NONE", False),
        Attribute("quantity", "EVENT", "NUMBER", "NONE", False),
        Attribute("store_id", "EVENT", "STRING", "NONE", False),
        Attribute("page_url", "EVENT", "STRING", "NONE", False),
        Attribute("event_name", "EVENT", "STRING", "NONE", False),
        Attribute("event_timestamp", "EVENT", "TIMESTAMP", "NONE", False),
        Attribute("ride_id", "EVENT", "STRING", "NONE", False),
        Attribute("ride_fare", "EVENT", "NUMBER", "NONE", False),
        Attribute("pickup_city", "EVENT", "STRING", "NONE", False),
    )
}

# Source type -> compatible canonical data types.
COMPATIBLE = {
    "STRING": {"STRING", "EMAIL", "PHONE"},
    "INTEGER": {"NUMBER"},
    "DECIMAL": {"NUMBER"},
    "DATE": {"DATE"},
    "TIMESTAMP": {"TIMESTAMP", "DATE"},
    "BOOLEAN": {"BOOLEAN"},
}


@dataclass(frozen=True)
class Connector:
    code: str
    label: str
    cadences: tuple[str, ...]
    min_business_days: int


CONNECTORS = {
    c.code: c
    for c in (
        Connector("SHOPIFY", "Shopify", ("HOURLY", "DAILY"), 5),
        Connector("SALESFORCE_CRM", "Salesforce CRM", ("HOURLY", "DAILY"), 8),
        Connector("SFTP_CSV", "SFTP CSV drop", ("DAILY", "WEEKLY", "ONE_TIME"), 3),
        Connector("SNOWFLAKE_SHARE", "Snowflake data share", ("HOURLY", "DAILY"), 10),
        Connector("GA4", "Google Analytics 4", ("DAILY",), 5),
        Connector("BRAZE", "Braze", ("REALTIME", "HOURLY"), 7),
        Connector("REST_WEBHOOK", "REST webhook", ("REALTIME",), 12),
    )
}


@dataclass(frozen=True)
class Agreement:
    number: str
    client: str
    start: date
    end: date
    status: str
    regions: tuple[str, ...]
    lawful_bases: tuple[str, ...]
    sensitive: tuple[str, ...]
    max_retention_days: int


AGREEMENTS = (
    Agreement(
        "DPA-2001",
        "Harbor & Vine Coffee Co.",
        D("2026-01-01"),
        D("2027-12-31"),
        "ACTIVE",
        ("US", "CA"),
        ("CONSENT", "CONTRACT", "LEGITIMATE_INTEREST"),
        (),
        730,
    ),
    Agreement(
        "DPA-1990",
        "Harbor & Vine Coffee Co.",
        D("2024-01-01"),
        D("2025-12-31"),
        "EXPIRED",
        ("US",),
        ("CONTRACT",),
        (),
        365,
    ),
    Agreement(
        "DPA-2002",
        "Lumen Home",
        D("2026-03-01"),
        D("2027-02-28"),
        "ACTIVE",
        ("US",),
        ("CONSENT", "CONTRACT"),
        (),
        1095,
    ),
    Agreement(
        "DPA-2003",
        "Petalworks Beauty",
        D("2025-06-01"),
        D("2026-11-30"),
        "ACTIVE",
        ("US",),
        ("CONSENT", "CONTRACT"),
        (),
        730,
    ),
    Agreement(
        "DPA-2004",
        "Kestrel Health",
        D("2026-09-01"),
        D("2028-08-31"),
        "ACTIVE",
        ("US",),
        ("CONSENT",),
        ("HEALTH",),
        730,
    ),
    Agreement(
        "DPA-2005",
        "Bluepoint Credit Union",
        D("2026-01-15"),
        D("2028-01-14"),
        "ACTIVE",
        ("US",),
        ("CONTRACT", "LEGITIMATE_INTEREST", "CONSENT"),
        (),
        2555,
    ),
    Agreement(
        "DPA-2006",
        "Orbit Rideshare",
        D("2026-06-01"),
        D("2027-05-31"),
        "ACTIVE",
        ("US", "CA"),
        ("CONSENT", "CONTRACT"),
        (),
        365,
    ),
    Agreement(
        "DPA-2007",
        "Alpenrose Travel",
        D("2026-04-01"),
        D("2027-03-31"),
        "ACTIVE",
        ("US",),
        ("CONSENT", "CONTRACT"),
        (),
        730,
    ),
    Agreement(
        "DPA-2008",
        "Northline Outdoor Gear",
        D("2026-02-01"),
        D("2027-01-31"),
        "ACTIVE",
        ("US",),
        ("CONSENT", "CONTRACT", "LEGITIMATE_INTEREST"),
        (),
        730,
    ),
)


@dataclass(frozen=True)
class Mapping:
    source_field: str
    source_type: str
    proposed_attribute: str | None
    client_marked_personal: bool
    description: str = ""


@dataclass
class Request:
    request_id: str
    client: str
    agreement_stated: str | None
    received: date
    contact: str
    contact_email: str
    source_name: str
    connector: str
    cadence: str | None
    region: str
    lawful_basis: str | None
    consent_mechanism: str | None
    declares_no_personal: bool
    declares_sensitive: bool
    retention_days: int
    estimated_records: int
    go_live: date | None
    data_owner: str
    mappings: list[Mapping]
    sample_unmasked: bool = False
    privacy_addendum: str | None = None
    email: str | None = None
    email_go_live: date | None = None
    email_added: list[Mapping] = field(default_factory=lambda: list[Mapping]())
    email_only: bool = False
    scanned: bool = False
    include_sample: bool = True
    duplicate_of: str | None = None
    edge_case: str = ""


def m(
    field_name: str, source_type: str, attribute: str | None, personal: bool, desc: str = ""
) -> Mapping:
    return Mapping(field_name, source_type, attribute, personal, desc)


ORDERS = [
    m("customer_email", "STRING", "email_address", True, "Buyer email"),
    m("order_number", "STRING", "order_id", False, "Shopify order name"),
    m("total_price", "DECIMAL", "order_total", False, "Order total, USD"),
    m("created_at", "TIMESTAMP", "order_timestamp", False, "Order time, UTC"),
    m("sku", "STRING", "product_sku", False, "Line item SKU"),
    m("qty", "INTEGER", "quantity", False, "Line item quantity"),
]

REQUESTS: list[Request] = [
    Request(
        "D01",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-02"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "Shopify orders (US store)",
        "SHOPIFY",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        730,
        1_200_000,
        D("2026-11-30"),
        "Elena Ruiz, Brand Director",
        ORDERS,
        edge_case="Baseline: clean order feed, masked sample",
    ),
    Request(
        "D02",
        "Kestrel Health",
        "DPA-2004",
        D("2026-11-03"),
        "Dr. Alan Mercer",
        "amercer@kestrelhealth.example",
        "Calmeris savings-card enrollees",
        "SALESFORCE_CRM",
        "DAILY",
        "US",
        "CONSENT",
        "Double opt-in at savings-card enrollment, recorded with timestamp and form version",
        False,
        True,
        365,
        85_000,
        D("2026-12-15"),
        "Dr. Alan Mercer, Brand Lead",
        [
            m("contact_id", "STRING", "customer_id", False, "CRM contact ID"),
            m("email", "STRING", "email_address", True, "Enrollee email"),
            m("condition_interest", "STRING", "health_condition", True, "Self-reported condition"),
            m("opted_in", "BOOLEAN", "marketing_opt_in", False, "Marketing consent flag"),
        ],
        privacy_addendum=(
            "Kestrel Health processes self-reported health interests only with explicit consent "
            "captured at savings-card enrollment. Data subjects may withdraw consent at any time; "
            "withdrawals propagate within 24 hours."
        ),
        edge_case="Sensitive health data that the agreement permits, with documented consent",
    ),
    Request(
        "D03",
        "Lumen Home",
        "DPA-2002",
        D("2026-11-03"),
        "Jonah Pratt",
        "jpratt@lumenhome.example",
        "Lumen web analytics",
        "GA4",
        None,
        "US",
        "CONSENT",
        "Cookie consent banner on lumenhome.example",
        False,
        False,
        395,
        4_000_000,
        None,
        "Jonah Pratt, Marketing Manager",
        [
            m("user_pseudo_id", "STRING", "device_id", True, "GA4 pseudonymous ID"),
            m("event_name", "STRING", "event_name", False, "GA4 event"),
            m("page_location", "STRING", "page_url", False, "Page URL"),
            m("event_timestamp", "TIMESTAMP", "event_timestamp", False, "Event time"),
        ],
        include_sample=False,
        edge_case="Cadence and go-live left as TBD",
    ),
    Request(
        "D04",
        "Northline Outdoor Gear",
        "DPA-2008",
        D("2026-11-04"),
        "Ben Ashworth",
        "bashworth@northline.example",
        "Store POS sales file",
        "SFTP_CSV",
        "DAILY",
        "US",
        None,
        None,
        True,
        False,
        730,
        300_000,
        D("2026-12-01"),
        "Ben Ashworth, Director of Brand",
        [
            m("txn_id", "STRING", "order_id", False, "POS transaction"),
            m("amount", "DECIMAL", "order_total", False, "Sale amount"),
            m("store", "STRING", "store_id", False, "Store code"),
        ],
        edge_case="No identity key: sales cannot be tied to a customer",
    ),
    Request(
        "D05",
        "Bluepoint Credit Union",
        "DPA-2005",
        D("2026-11-04"),
        "Nadia Haddad",
        "nhaddad@bluepointcu.example",
        "Member engagement scores",
        "SNOWFLAKE_SHARE",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        1825,
        140_000,
        D("2026-12-14"),
        "Nadia Haddad, Marketing Director",
        [
            m("member_id", "STRING", "customer_id", False, "Member number (hashed)"),
            m("balance_band", "STRING", "account_balance_band", False, "Balance band A-E"),
            m("member_vibe_score", "DECIMAL", None, False, "Internal engagement score"),
        ],
        edge_case="A field with no canonical attribute",
    ),
    Request(
        "D06",
        "Orbit Rideshare",
        "DPA-2006",
        D("2026-11-05"),
        "Lucas Ferreira",
        "lferreira@orbitrides.example",
        "Rider sign-ups",
        "SALESFORCE_CRM",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        365,
        600_000,
        D("2026-12-18"),
        "Lucas Ferreira, Head of Marketing",
        [
            m("rider_id", "STRING", "customer_id", False, "Rider ID"),
            m("signup_date", "DATE", "loyalty_tier", False, "Date the rider signed up"),
            m("home_city", "STRING", "pickup_city", False, "Home city"),
        ],
        edge_case="Client mapped a date to a text attribute",
    ),
    Request(
        "D07",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-05"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "Loyalty member file",
        "SFTP_CSV",
        "REALTIME",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        730,
        450_000,
        D("2026-12-07"),
        "Elena Ruiz, Brand Director",
        [
            m("loyalty_number", "STRING", "loyalty_id", True, "Loyalty card number"),
            m("tier", "STRING", "loyalty_tier", False, "Bronze, Silver, Gold"),
            m("joined", "TIMESTAMP", "account_created_at", False, "Join time"),
        ],
        edge_case="Real-time cadence on a file-drop connector",
    ),
    Request(
        "D08",
        "Fernhill Grocers",
        None,
        D("2026-11-06"),
        "Grace Liu",
        "gliu@fernhill.example",
        "Fernhill e-commerce orders",
        "SHOPIFY",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        365,
        900_000,
        D("2026-12-21"),
        "Grace Liu, Digital Director",
        ORDERS,
        edge_case="New client with no data agreement",
    ),
    Request(
        "D09",
        "Petalworks Beauty",
        "DPA-2003",
        D("2026-11-06"),
        "Camille Dubois",
        "cdubois@petalworks.example",
        "Petalworks DTC orders",
        "SHOPIFY",
        "HOURLY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        730,
        250_000,
        D("2026-12-14"),
        "Camille Dubois, VP Marketing",
        ORDERS,
        edge_case="Agreement expires before go-live",
    ),
    Request(
        "D10",
        "Alpenrose Travel",
        "DPA-2007",
        D("2026-11-09"),
        "Lena Hofmann",
        "lhofmann@alpenrose.example",
        "EU booking contacts",
        "SALESFORCE_CRM",
        "DAILY",
        "EU",
        "CONSENT",
        "Checkbox at booking, logged by booking engine",
        False,
        False,
        365,
        210_000,
        D("2027-01-11"),
        "Lena Hofmann, CRM Lead",
        [
            m("booking_contact_id", "STRING", "customer_id", False, "Contact ID"),
            m("email", "STRING", "email_address", True, "Traveler email"),
            m("country", "STRING", "country_code", False, "Country of residence"),
        ],
        edge_case="EU data under a US-only agreement",
    ),
    Request(
        "D11",
        "Lumen Home",
        "DPA-2002",
        D("2026-11-09"),
        "Jonah Pratt",
        "jpratt@lumenhome.example",
        "Braze customer profiles",
        "BRAZE",
        "REALTIME",
        "US",
        None,
        None,
        True,
        False,
        1095,
        520_000,
        D("2026-12-14"),
        "Jonah Pratt, Marketing Manager",
        [
            m("external_id", "STRING", "customer_id", False, "Braze external ID"),
            m("contact", "STRING", "email_address", False, "Primary contact"),
            m("mobile", "STRING", "phone_number", False, "Mobile"),
            m("tier", "STRING", "loyalty_tier", False, "Member tier"),
        ],
        sample_unmasked=True,
        edge_case="Form says no personal data; sample extract has unmasked emails and phones",
    ),
    Request(
        "D12",
        "Orbit Rideshare",
        "DPA-2006",
        D("2026-11-10"),
        "Lucas Ferreira",
        "lferreira@orbitrides.example",
        "Rider accessibility profiles",
        "BRAZE",
        "HOURLY",
        "US",
        "CONSENT",
        "In-app accessibility settings opt-in",
        False,
        True,
        365,
        40_000,
        D("2026-12-21"),
        "Lucas Ferreira, Head of Marketing",
        [
            m("rider_id", "STRING", "customer_id", False, "Rider ID"),
            m("accessibility", "STRING", "accessibility_needs", True, "Wheelchair, service animal"),
            m("city", "STRING", "pickup_city", False, "Home city"),
        ],
        edge_case="Sensitive data the agreement does not permit",
    ),
    Request(
        "D13",
        "Bluepoint Credit Union",
        "DPA-2005",
        D("2026-11-10"),
        "Nadia Haddad",
        "nhaddad@bluepointcu.example",
        "Member contact preferences",
        "SALESFORCE_CRM",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        3650,
        140_000,
        D("2027-01-04"),
        "Nadia Haddad, Marketing Director",
        [
            m("member_id", "STRING", "customer_id", False, "Member number (hashed)"),
            m("email", "STRING", "email_address", True, "Member email"),
            m("opt_in", "BOOLEAN", "marketing_opt_in", False, "Email opt-in"),
        ],
        edge_case="Retention of 10 years exceeds the 7-year agreement maximum",
    ),
    Request(
        "D14",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-11"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "Braze app users",
        "BRAZE",
        "HOURLY",
        "US",
        "CONSENT",
        None,
        False,
        False,
        730,
        380_000,
        D("2026-12-28"),
        "Elena Ruiz, Brand Director",
        [
            m("braze_id", "STRING", "customer_id", False, "Braze user ID"),
            m("email", "STRING", "email_address", True, "App account email"),
            m("push_opt_in", "BOOLEAN", "marketing_opt_in", False, "Push opt-in"),
        ],
        edge_case="Consent basis with no consent mechanism described",
    ),
    Request(
        "D15",
        "Northline Outdoor Gear",
        "DPA-2008",
        D("2026-11-04"),
        "Ben Ashworth",
        "bashworth@northline.example",
        "Warehouse customer share",
        "SNOWFLAKE_SHARE",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        730,
        700_000,
        D("2026-11-12"),
        "Ben Ashworth, Director of Brand",
        [
            m("cust_id", "STRING", "customer_id", False, "Customer ID"),
            m("email", "STRING", "email_address", True, "Customer email"),
            m("zip", "STRING", "postal_code", True, "Postal code"),
        ],
        edge_case="Go-live in 6 business days; Snowflake shares need 10",
    ),
    Request(
        "D16",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-06"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "CRM café members",
        "SALESFORCE_CRM",
        "DAILY",
        "US",
        "CONSENT",
        "Opt-in at café Wi-Fi sign-up",
        False,
        False,
        730,
        260_000,
        D("2026-12-21"),
        "Elena Ruiz, Brand Director",
        [
            m("member_id", "STRING", "customer_id", False, "CRM member ID"),
            m("email", "STRING", "email_address", True, "Member email"),
            m("first_name", "STRING", "first_name", True, "First name"),
            m("last_name", "STRING", "last_name", True, "Last name"),
        ],
        email=(
            "Hi team,\n\nThe holiday loyalty push moved up. Can the café members feed be live by "
            "November 17 instead of December 21? Same fields.\n\nThanks,\nElena"
        ),
        email_go_live=D("2026-11-17"),
        edge_case="Email moves go-live earlier than the CRM connector can be built",
    ),
    Request(
        "D17",
        "Orbit Rideshare",
        "DPA-2006",
        D("2026-11-11"),
        "Lucas Ferreira",
        "lferreira@orbitrides.example",
        "Ride history events",
        "SNOWFLAKE_SHARE",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        365,
        9_000_000,
        D("2027-01-11"),
        "Lucas Ferreira, Head of Marketing",
        [
            m("rider_id", "STRING", "customer_id", False, "Rider ID"),
            m("ride_id", "STRING", "ride_id", False, "Ride ID"),
            m("fare", "DECIMAL", "ride_fare", False, "Fare, USD"),
            m("pickup_city", "STRING", "pickup_city", False, "Pickup city"),
        ],
        email=(
            "Hi Meridian & Pike Data,\n\nOne more column for the ride history share: loyalty_tier "
            "(Orbit One status). Same go-live.\n\nLucas"
        ),
        email_added=[m("loyalty_tier", "STRING", "loyalty_tier", False, "Orbit One status")],
        edge_case="Email adds a field the dictionary does not list",
    ),
    Request(
        "D18",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-12"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "Shopify orders (US store)",
        "SHOPIFY",
        "DAILY",
        "US",
        "CONTRACT",
        None,
        False,
        False,
        730,
        1_200_000,
        D("2026-11-30"),
        "Elena Ruiz, Brand Director",
        ORDERS,
        email_only=True,
        include_sample=False,
        duplicate_of="D01",
        edge_case="Email-only resubmission of D01",
    ),
    Request(
        "D19",
        "Harbor & Vine Coffee Co.",
        "DPA-2001",
        D("2026-11-10"),
        "Elena Ruiz",
        "elena.ruiz@harborvine.example",
        "Website analytics",
        "GA4",
        "DAILY",
        "US",
        "LEGITIMATE_INTEREST",
        None,
        False,
        False,
        395,
        6_000_000,
        D("2026-12-07"),
        "Elena Ruiz, Brand Director",
        [
            m("user_pseudo_id", "STRING", "device_id", True, "GA4 pseudonymous ID"),
            m("event_name", "STRING", "event_name", False, "GA4 event"),
            m("page_location", "STRING", "page_url", False, "Page URL"),
        ],
        scanned=True,
        include_sample=False,
        edge_case="Degraded scan of the onboarding form",
    ),
    Request(
        "D20",
        "Lumen Home",
        "DPA-2002",
        D("2026-11-09"),
        "Jonah Pratt",
        "jpratt@lumenhome.example",
        "Warranty registrations backfill",
        "SFTP_CSV",
        "ONE_TIME",
        "US",
        None,
        None,
        True,
        False,
        365,
        75_000,
        D("2026-11-12"),
        "Jonah Pratt, Marketing Manager",
        [
            m("registration_id", "STRING", "customer_id", False, "Registration ID"),
            m("country", "STRING", "country_code", False, "Country"),
            m("registered_at", "TIMESTAMP", "account_created_at", False, "Registration time"),
            m("promo_opt_in", "BOOLEAN", "marketing_opt_in", False, "Promotions opt-in"),
        ],
        edge_case="Boundary: go-live exactly at the 3-business-day SFTP minimum",
    ),
]
