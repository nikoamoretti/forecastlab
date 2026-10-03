"""Country identifiers for the panel.

The panel key is UCDP GED ``country_id``, a Gleditsch–Ward (G&W) state code. This module
adds ISO 3166-1 alpha-3 codes for the present-day state where one exists, and the months
in which states that entered or left the system after 1989 are in the panel, so months
before a state existed are not counted as observed zeros.
"""

from __future__ import annotations

from forecastlab.conflict.months import parse_month

# G&W code -> ISO 3166-1 alpha-3 of the present-day state. Codes without an ISO 3166-1
# entry (Kosovo, and states that no longer exist) are deliberately absent. GED 345
# "Serbia (Yugoslavia)", 365 "Russia (Soviet Union)" and 678 "Yemen (North Yemen)" map
# to today's Serbia, Russia and Yemen.
GW_ISO3: dict[int, str] = {
    2: "USA", 20: "CAN", 31: "BHS", 40: "CUB", 41: "HTI", 42: "DOM", 51: "JAM", 52: "TTO",
    53: "BRB", 54: "DMA", 55: "GRD", 56: "LCA", 57: "VCT", 58: "ATG", 60: "KNA", 70: "MEX",
    80: "BLZ", 90: "GTM", 91: "HND", 92: "SLV", 93: "NIC", 94: "CRI", 95: "PAN", 100: "COL",
    101: "VEN", 110: "GUY", 115: "SUR", 130: "ECU", 135: "PER", 140: "BRA", 145: "BOL",
    150: "PRY", 155: "CHL", 160: "ARG", 165: "URY", 200: "GBR", 205: "IRL", 210: "NLD",
    211: "BEL", 212: "LUX", 220: "FRA", 221: "MCO", 223: "LIE", 225: "CHE", 230: "ESP",
    232: "AND", 235: "PRT", 260: "DEU", 290: "POL", 305: "AUT", 310: "HUN", 316: "CZE",
    317: "SVK", 325: "ITA", 331: "SMR", 338: "MLT", 339: "ALB", 340: "SRB", 341: "MNE",
    343: "MKD", 344: "HRV", 345: "SRB", 346: "BIH", 349: "SVN", 350: "GRC", 352: "CYP",
    355: "BGR", 359: "MDA", 360: "ROU", 365: "RUS", 366: "EST", 367: "LVA", 368: "LTU",
    369: "UKR", 370: "BLR", 371: "ARM", 372: "GEO", 373: "AZE", 375: "FIN", 380: "SWE",
    385: "NOR", 390: "DNK", 395: "ISL", 402: "CPV", 403: "STP", 404: "GNB", 411: "GNQ",
    420: "GMB", 432: "MLI", 433: "SEN", 434: "BEN", 435: "MRT", 436: "NER", 437: "CIV",
    438: "GIN", 439: "BFA", 450: "LBR", 451: "SLE", 452: "GHA", 461: "TGO", 471: "CMR",
    475: "NGA", 481: "GAB", 482: "CAF", 483: "TCD", 484: "COG", 490: "COD", 500: "UGA",
    501: "KEN", 510: "TZA", 516: "BDI", 517: "RWA", 520: "SOM", 522: "DJI", 530: "ETH",
    531: "ERI", 540: "AGO", 541: "MOZ", 551: "ZMB", 552: "ZWE", 553: "MWI", 560: "ZAF",
    565: "NAM", 570: "LSO", 571: "BWA", 572: "SWZ", 580: "MDG", 581: "COM", 590: "MUS",
    591: "SYC", 600: "MAR", 615: "DZA", 616: "TUN", 620: "LBY", 625: "SDN", 626: "SSD",
    630: "IRN", 640: "TUR", 645: "IRQ", 651: "EGY", 652: "SYR", 660: "LBN", 663: "JOR",
    666: "ISR", 670: "SAU", 678: "YEM", 690: "KWT", 692: "BHR", 694: "QAT", 696: "ARE",
    698: "OMN", 700: "AFG", 701: "TKM", 702: "TJK", 703: "KGZ", 704: "UZB", 705: "KAZ",
    710: "CHN", 712: "MNG", 713: "TWN", 731: "PRK", 732: "KOR", 740: "JPN", 750: "IND",
    760: "BTN", 770: "PAK", 771: "BGD", 775: "MMR", 780: "LKA", 781: "MDV", 790: "NPL",
    800: "THA", 811: "KHM", 812: "LAO", 816: "VNM", 820: "MYS", 830: "SGP", 835: "BRN",
    840: "PHL", 850: "IDN", 860: "TLS", 900: "AUS", 910: "PNG", 920: "NZL", 935: "VUT",
    940: "SLB", 950: "FJI", 970: "KIR", 971: "NRU", 972: "TON", 973: "TUV", 983: "MHL",
    986: "PLW", 987: "FSM", 990: "WSM",
}

# First panel month for states that entered the system after January 1989, following the
# month UCDP starts coding events under the new state: broad recognition for Yugoslav
# successors, and the dissolution of the USSR (December 1991) for former Soviet
# republics other than the Baltic states. These are approximate entry months used only
# to avoid counting pre-independence months as observed zeros.
STATE_ENTRY_MONTH: dict[int, str] = {
    565: "1990-03",  # Namibia
    366: "1991-09",  # Estonia
    367: "1991-09",  # Latvia
    368: "1991-09",  # Lithuania
    359: "1991-12",  # Moldova
    369: "1991-12",  # Ukraine
    370: "1991-12",  # Belarus
    371: "1991-12",  # Armenia
    372: "1991-12",  # Georgia
    373: "1991-12",  # Azerbaijan
    701: "1991-12",  # Turkmenistan
    702: "1991-12",  # Tajikistan
    703: "1991-12",  # Kyrgyzstan
    704: "1991-12",  # Uzbekistan
    705: "1991-12",  # Kazakhstan
    344: "1992-01",  # Croatia
    349: "1992-01",  # Slovenia
    346: "1992-04",  # Bosnia-Herzegovina
    316: "1993-01",  # Czech Republic
    317: "1993-01",  # Slovakia
    343: "1993-04",  # North Macedonia
    531: "1993-05",  # Eritrea
    860: "2002-05",  # Timor-Leste
    341: "2006-06",  # Montenegro
    347: "2008-02",  # Kosovo
    626: "2011-07",  # South Sudan
}

# Last panel month for states that left the system after January 1989.
STATE_EXIT_MONTH: dict[int, str] = {
    680: "1990-05",  # South Yemen
    265: "1990-10",  # German Democratic Republic
    315: "1992-12",  # Czechoslovakia
}

# ViEWS codes Serbia after 2006 as 340; UCDP GED keeps 345 "Serbia (Yugoslavia)".
VIEWS_GWCODE_TO_GED: dict[int, int] = {340: 345}


def iso3_for(country_id: int) -> str | None:
    return GW_ISO3.get(int(country_id))


def existence_window(country_id: int, first_month: int, last_month: int) -> tuple[int, int]:
    """Months (inclusive) in which ``country_id`` exists, clipped to the panel range."""
    start = first_month
    end = last_month
    entry = STATE_ENTRY_MONTH.get(int(country_id))
    if entry is not None:
        start = max(start, parse_month(entry))
    exit_month = STATE_EXIT_MONTH.get(int(country_id))
    if exit_month is not None:
        end = min(end, parse_month(exit_month))
    return start, end


def ged_country_for_views(gwcode: int) -> int:
    return VIEWS_GWCODE_TO_GED.get(int(gwcode), int(gwcode))
