"""
Law abbreviation to search title map.
Used to turn "ABGB" into the Titel param for BrKons queries.
"""

LAW_ALIASES: dict[str, str] = {
    "ABGB": "Allgemeines bürgerliches Gesetzbuch",
    "StGB": "Strafgesetzbuch",
    "UGB": "Unternehmensgesetzbuch",
    "ASVG": "Allgemeines Sozialversicherungsgesetz",
    "GmbHG": "GmbH-Gesetz",
    "AktG": "Aktiengesetz",
    "ZPO": "Zivilprozessordnung",
    "StPO": "Strafprozeßordnung",
    "IO": "Insolvenzordnung",
    "KO": "Konkursordnung",
    "EO": "Exekutionsordnung",
    "AO": "Ausgleichsordnung",
    "BSVG": "Bauern-Sozialversicherungsgesetz",
    "GSVG": "Gewerbliches Sozialversicherungsgesetz",
    "BVG": "Bundes-Verfassungsgesetz",
    "B-VG": "Bundes-Verfassungsgesetz",
    "VwGG": "Verwaltungsgerichtshofgesetz",
    "VfGG": "Verfassungsgerichtshofgesetz",
    "AVG": "Allgemeines Verwaltungsverfahrensgesetz",
    "VStG": "Verwaltungsstrafgesetz",
    "VVG": "Verwaltungsvollstreckungsgesetz",
    "BAO": "Bundesabgabenordnung",
    "EStG": "Einkommensteuergesetz",
    "KStG": "Körperschaftsteuergesetz",
    "UStG": "Umsatzsteuergesetz",
    "GrEStG": "Grunderwerbsteuergesetz",
    "ArbVG": "Arbeitsverfassungsgesetz",
    "AngG": "Angestelltengesetz",
    "GewO": "Gewerbeordnung",
    "WRG": "Wasserrechtsgesetz",
    "ForstG": "Forstgesetz",
    "TKG": "Telekommunikationsgesetz",
    "DSG": "Datenschutzgesetz",
    "MedienG": "Mediengesetz",
    "MRG": "Mietrechtsgesetz",
    "WEG": "Wohnungseigentumsgesetz",
    "BauO": "Bauordnung",
    "ABGB": "Allgemeines bürgerliches Gesetzbuch",
    "SPG": "Sicherheitspolizeigesetz",
    "FremdenpolizeiG": "Fremdenpolizeigesetz",
    "AsylG": "Asylgesetz",
    "NAG": "Niederlassungs- und Aufenthaltsgesetz",
    "BDG": "Beamten-Dienstrechtsgesetz",
    "VBO": "Vertragsbedienstetengesetz",
    "AVRAG": "Arbeitsvertragsrechts-Anpassungsgesetz",
    "KSchG": "Konsumentenschutzgesetz",
    "PHG": "Produkthaftungsgesetz",
    "ECG": "E-Commerce-Gesetz",
    "UWG": "Bundesgesetz gegen den unlauteren Wettbewerb",
    "KartellG": "Kartellgesetz",
    "PatG": "Patentgesetz",
    "MarkSchG": "Markenschutzgesetz",
    "MSchG": "Markenschutzgesetz",
    "UrheberrechtsG": "Urheberrechtsgesetz",
    "UrhG": "Urheberrechtsgesetz",
    "OGHG": "Bundesgesetz über den Obersten Gerichtshof",
}


def resolve_law_name(name: str) -> str:
    """Return search title for a law name or abbreviation."""
    upper = name.upper().strip()
    if upper in LAW_ALIASES:
        return LAW_ALIASES[upper]
    for key, val in LAW_ALIASES.items():
        if key.upper() == upper:
            return val
    return name
