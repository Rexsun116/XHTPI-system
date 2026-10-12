"""Project-local ISO 3166-1 alpha-2 membership data.

The set is the complete 249-code alpha-2 list from IANA tzdata's
``iso3166.tab`` (which republishes the ISO 3166-1 assignments), captured for
deterministic offline application and test behavior.  Refresh this file from
the upstream IANA/ISO publication when ISO publishes a change.
"""

from pathlib import Path


ISO_3166_1_ALPHA2 = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ
    BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO
    CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM
    FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN
    HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP
    KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML
    MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR
    NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA
    SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG
    TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN
    VU WF WS YE YT ZA ZM ZW
    """.split()
)


# English display names were generated from Node.js Intl.DisplayNames using the
# Unicode CLDR region data, then committed as static data so runtime has no
# Node, network, or locale-data dependency.  Refresh from current CLDR/IANA
# source data when ISO assignments or English names are revised.
ISO_3166_1_ENGLISH_NAMES = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua & Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Åland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia & Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "St. Barthélemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Caribbean Netherlands",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BV": "Bouvet Island",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos (Keeling) Islands",
    "CD": "Congo - Kinshasa",
    "CF": "Central African Republic",
    "CG": "Congo - Brazzaville",
    "CH": "Switzerland",
    "CI": "Côte d’Ivoire",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cape Verde",
    "CW": "Curaçao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia & South Sandwich Islands",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong SAR China",
    "HM": "Heard & McDonald Islands",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "St. Kitts & Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "St. Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "St. Martin",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar (Burma)",
    "MN": "Mongolia",
    "MO": "Macao SAR China",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "St. Pierre & Miquelon",
    "PN": "Pitcairn Islands",
    "PR": "Puerto Rico",
    "PS": "Palestinian Territories",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Réunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "St. Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard & Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "São Tomé & Príncipe",
    "SV": "El Salvador",
    "SX": "Sint Maarten",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TC": "Turks & Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TR": "Türkiye",
    "TT": "Trinidad & Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UM": "U.S. Outlying Islands",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Vatican City",
    "VC": "St. Vincent & Grenadines",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "U.S. Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis & Futuna",
    "WS": "Samoa",
    "YE": "Yemen",
    "YT": "Mayotte",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe"
}


# Discovery aliases are deliberately separate from canonical display names and
# ISO identities.  They support familiar names where the CLDR display label is
# intentionally disambiguated; they are never persisted.
COUNTRY_SEARCH_ALIASES = {
    "CG": ("Congo", "Republic of the Congo", "Congo Brazzaville", "Brazzaville"),
    "CD": ("Congo", "Democratic Republic of the Congo", "DR Congo", "DRC", "Congo Kinshasa", "Kinshasa"),
}


def _normalize_country_name(value):
    return " ".join(str(value or "").split()).casefold()


def _accepted_country_codes_by_name():
    accepted = {}
    for code, name in ISO_3166_1_ENGLISH_NAMES.items():
        for value in (name, *COUNTRY_SEARCH_ALIASES.get(code, ())):
            accepted.setdefault(_normalize_country_name(value), set()).add(code)
    return {name: frozenset(codes) for name, codes in accepted.items()}


_COUNTRY_CODES_BY_NORMALIZED_ACCEPTED_NAME = _accepted_country_codes_by_name()
_FLAG_ASSET_DIRECTORY = Path(__file__).parent / "static" / "flags"


def country_name_for_code(code):
    """Return the exact English display name for an explicit alpha-2 code."""
    normalized = str(code or "").strip().upper()
    return ISO_3166_1_ENGLISH_NAMES.get(normalized)


def country_flag_for_code(code):
    """Return a decorative regional-indicator flag for one valid ISO alpha-2 code."""
    normalized = str(code or "").strip().upper()
    if (len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha()
            or normalized not in ISO_3166_1_ALPHA2):
        return None
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in normalized)


def country_flag_asset_for_code(code):
    """Return a verified local SVG asset path for one valid ISO alpha-2 code."""
    normalized = str(code or "").strip().upper()
    if (len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha()
            or normalized not in ISO_3166_1_ALPHA2):
        return None
    filename = f"{normalized.lower()}.svg"
    return f"flags/{filename}" if (_FLAG_ASSET_DIRECTORY / filename).is_file() else None


def country_code_for_name(value):
    """Resolve an exact accepted name only when it identifies one ISO code."""
    codes = _COUNTRY_CODES_BY_NORMALIZED_ACCEPTED_NAME.get(_normalize_country_name(value), ())
    return next(iter(codes)) if len(codes) == 1 else None


def _country_search_rank(query, *, name, aliases):
    normalized_name = _normalize_country_name(name)
    if query == normalized_name:
        return 0
    if normalized_name.startswith(query):
        return 1
    if query in normalized_name:
        return 2
    normalized_aliases = [_normalize_country_name(alias) for alias in aliases]
    if query in normalized_aliases:
        return 3
    if any(alias.startswith(query) for alias in normalized_aliases):
        return 4
    if any(query in alias for alias in normalized_aliases):
        return 5
    return None


def search_countries(value, *, limit=10):
    """Return deterministic display rows, ranked before applying the cap."""
    query = _normalize_country_name(value)
    if not query or limit <= 0:
        return []
    matches = []
    for code, name in ISO_3166_1_ENGLISH_NAMES.items():
        aliases = COUNTRY_SEARCH_ALIASES.get(code, ())
        rank = _country_search_rank(query, name=name, aliases=aliases)
        if rank is not None:
            matches.append((rank, {"code": code, "name": name, "aliases": list(aliases)}))
    matches.sort(key=lambda result: (result[0], result[1]["name"].casefold(), result[1]["code"]))
    return [country for _, country in matches[:limit]]


def country_choices():
    """Return all display rows with optional discovery aliases."""
    return sorted(
        ({"code": code, "name": name, "aliases": list(COUNTRY_SEARCH_ALIASES.get(code, ()))}
         for code, name in ISO_3166_1_ENGLISH_NAMES.items()),
        key=lambda country: (country["name"].casefold(), country["code"]),
    )
