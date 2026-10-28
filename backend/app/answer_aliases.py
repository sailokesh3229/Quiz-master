"""Conservative, reviewed name/formula equivalents for short-answer grading.

Unknown compounds are not guessed. Formula case is meaningful (CO != Co).
Lowercase student input is accepted only when the registry is unambiguous.
"""
import re
import unicodedata

ELEMENTS = set("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())

CHEMICAL_ALIASES = {
    "magnesium oxide": ("MgO",),
    "water": ("H2O",),
    "hydrogen peroxide": ("H2O2",),
    "carbon dioxide": ("CO2",),
    "carbon monoxide": ("CO",),
    "cobalt": ("Co",),
    "sodium chloride": ("NaCl",),
    "calcium oxide": ("CaO", "quicklime"),
    "calcium hydroxide": ("Ca(OH)2", "slaked lime"),
    "calcium carbonate": ("CaCO3",),
    "sodium hydroxide": ("NaOH",),
    "sodium carbonate": ("Na2CO3",),
    "sodium hydrogen carbonate": ("NaHCO3", "sodium bicarbonate"),
    "sulfuric acid": ("H2SO4", "sulphuric acid"),
    "hydrochloric acid": ("HCl",),
    "zinc sulfate": ("ZnSO4", "zinc sulphate"),
}


def normalize_answer(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())


def looks_like_formula(value: str) -> bool:
    value = normalize_answer(value)
    return bool(re.fullmatch(r"(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+", value)) and all(
        symbol in ELEMENTS for symbol in re.findall(r"[A-Z][a-z]?", value)
    )


def chemical_identity(value: str) -> str | None:
    value = normalize_answer(value)
    # First compare names without case changes to formula notation.
    for name, aliases in CHEMICAL_ALIASES.items():
        if value.casefold() == name or any(
            not looks_like_formula(a) and value.casefold() == a.casefold() for a in aliases
        ):
            return name
    exact = {name for name, aliases in CHEMICAL_ALIASES.items() if value in aliases}
    if len(exact) == 1:
        return next(iter(exact))
    if value == value.lower():
        candidates = {name for name, aliases in CHEMICAL_ALIASES.items()
                      if any(looks_like_formula(a) and value == a.lower() for a in aliases)}
        if len(candidates) == 1:
            return next(iter(candidates))
    return None


def accepted_answers_for(payload: dict) -> list[str]:
    answer = normalize_answer(payload["answer"])
    identity = chemical_identity(answer)
    if identity:
        # Reviewed registry overrides model-proposed chemical alternatives.
        candidates = [identity, *CHEMICAL_ALIASES[identity]]
    else:
        candidates = payload.get("accepted_answers") or []
    result = []
    for value in candidates:
        if not isinstance(value, str):
            continue
        value = normalize_answer(value)
        if value and value != answer and value not in result:
            result.append(value)
    return result
