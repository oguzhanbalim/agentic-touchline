"""Decide, before any model call, whether a player's question is outside the coach's remit.

Medical, contract and transfer questions go to staff. This layer is keyword rules, so it is
cheap, deterministic and testable offline, and it misses paraphrases. The coach's system
prompt is the second layer for those, and that layer has not been measured.
"""

from __future__ import annotations

import re
from enum import StrEnum

from touchline.metrics.player import fold_name


class Route(StrEnum):
    COACHING = "coaching"
    MEDICAL = "medical"
    CONTRACT = "contract"
    TRANSFER = "transfer"


# Football phrases that contain an out-of-scope word and mean something else. They are
# blanked out before the rules run. Patterns are applied to folded text (see fold_name).
_NOT_OUT_OF_SCOPE = (
    r"injury[- ]time",
    r"tiempo de (?:descuento|lesion(?:es)?|compensacion)",
    r"transfer(?:ring)? (?:the ball|of play|the play|it)",
    r"\bcontract(?:s|ed|ing)? the (?:block|space|shape|pitch)",
    r"\bhurt (?:them|the (?:opposition|opponent|defen[cs]e|back line|defenders))\b",
    r"\bpain(?:ful)? for (?:them|the (?:opposition|defen[cs]e))\b",
    r"(?:dolor de cabeza|bas agrisi) (?:para|icin)",
    r"\bagent of\b",
)

_RULES: tuple[tuple[Route, tuple[str, ...]], ...] = (
    (
        Route.MEDICAL,
        (
            # English
            r"\binjur", r"\bhurts?\b", r"\bpain", r"\bsore\b", r"\bdoctor", r"\bphysio",
            r"\bmedic", r"\bsurgery\b", r"\bconcuss", r"\bhamstring", r"\bgroin\b", r"\bpills?\b",
            r"\bpainkiller", r"\brehab", r"\bsprain", r"\bmy (?:knee|ankle|back|calf|thigh|foot)\b",
            r"\bdizz", r"\btreatment\b", r"\bsupplement",
            # Spanish
            r"\blesion", r"\bdolor", r"\bduele", r"\bmedico", r"\bfisio", r"\bmedicina",
            r"\boperacion de", r"\bconmocion", r"\bisquio", r"\brodilla", r"\btobillo",
            r"\btratamiento\b", r"\bpastilla", r"\bmareo", r"\brecuperacion de",
            # Turkish
            r"\bsakat", r"\bagri", r"\bagriy", r"\bdoktor", r"\bilac", r"\btedavi", r"\bameliyat",
            r"\bfizyoterap", r"\bdizim", r"\bbilegim", r"\bbelim\b",
            r"\bkasim(?:da)? (?:cekil|yirt)",
            r"\barka adale", r"\bbas donme", r"\bsersem",
        ),
    ),
    (
        Route.CONTRACT,
        (
            # English
            r"\bcontract", r"\bsalar", r"\bwages?\b", r"\bpay rise\b", r"\brenew", r"\bbonus",
            r"\brelease clause", r"\bbuy-?out\b",
            # Spanish
            r"\bcontrato", r"\bsueldo", r"\bsalario", r"\brenova", r"\bclausula", r"\bprima\b",
            r"\bprimas\b", r"\bficha\b",
            # Turkish
            r"\bsozlesme", r"\bkontrat", r"\bmaas", r"\bucret", r"\bprim\b", r"\bprimi\b",
            r"\bprimler", r"\bserbest kalma", r"\buzatma imza", r"\bimza at",
        ),
    ),
    (
        Route.TRANSFER,
        (
            # English
            r"\btransfer", r"\bmy agent\b", r"\bagent says", r"\bsign for\b", r"\bon loan\b",
            r"\bloan (?:move|deal|out)", r"\bleave the club\b", r"\bleave this club\b",
            r"\bother clubs?\b.*\b(?:want|interest|bid|offer)", r"\bbids? for me\b",
            r"\bmove to (?:another|a bigger|a different) club\b", r"\bjoin (?:another|a) club\b",
            # Spanish
            r"\bfichaje", r"\bfichar por\b", r"\bme fichan\b", r"\bficharme\b", r"\btraspaso",
            r"\bmi agente\b", r"\bmi representante\b", r"\bcesion\b", r"\bcedido\b",
            r"\botros? clubes?\b", r"\botro club\b", r"\bdejar el club\b", r"\bme voy del club\b",
            # Turkish
            r"\btransfer", r"\bmenajer", r"\bbonservis", r"\bkiralik", r"\bbaska (?:bir )?kulup",
            r"\bkulupten ayril", r"\bbaska takim", r"\bteklif (?:gel|var)",
        ),
    ),
)  # fmt: skip

_COMPILED = tuple((route, re.compile("|".join(patterns))) for route, patterns in _RULES)
_EXCEPTIONS = re.compile("|".join(_NOT_OUT_OF_SCOPE))

_TURKISH_LETTERS = re.compile("[ğışİ]")
_SPANISH_LETTERS = re.compile("[ñ¿¡]")
_TURKISH_WORDS = re.compile(
    r"\b(?:nerede|nasil|neden|hangi|benim|bana|topu|ben|icin|var|kac|miyim|miyiz|maci\w*|rolum)\b"
)
_SPANISH_WORDS = re.compile(
    r"\b(?:donde|como|debo|que|porque|balon|pelota|tengo|por|recibir|cual|para|cuantos|mi|el|la)\b"
)
_ENGLISH_WORDS = re.compile(
    r"\b(?:the|where|how|should|what|why|my|is|do|am|which|does|with|in|of|did)\b"
)


def route_question(question: str) -> Route:
    """Return the route. Medical wins over contract, which wins over transfer."""
    folded = fold_name(question)
    cleaned = _EXCEPTIONS.sub(" ", folded)
    for route, pattern in _COMPILED:
        if pattern.search(cleaned):
            return route
    return Route.COACHING


def detect_language(text: str) -> str:
    """Pick tr, es or en by counting markers. Used to choose a template, never to translate."""
    folded = fold_name(text)
    scores = {
        "tr": 2 * len(_TURKISH_LETTERS.findall(text)) + len(_TURKISH_WORDS.findall(folded)),
        "es": 2 * len(_SPANISH_LETTERS.findall(text)) + len(_SPANISH_WORDS.findall(folded)),
        "en": len(_ENGLISH_WORDS.findall(folded)),
    }
    best = max(scores, key=lambda code: scores[code])
    return best if scores[best] > 0 else "en"


_REFERRALS: dict[Route, dict[str, str]] = {
    Route.MEDICAL: {
        "en": "I can't help with medical questions. Please speak to the team doctor or "
        "physiotherapist. I can help with your role and where you receive the ball.",
        "es": "No puedo ayudarte con cuestiones médicas. Habla con el médico o el fisioterapeuta "
        "del equipo. Sí puedo ayudarte con tu rol y con dónde recibes el balón.",
        "tr": "Sağlık konularında yardımcı olamam. Lütfen takım doktoru veya fizyoterapistle "
        "görüş. Rolün ve topu nerede aldığın konusunda yardımcı olabilirim.",
    },
    Route.CONTRACT: {
        "en": "I can't help with contract questions. Please speak to club staff or your "
        "representative. I can help with your role and where you receive the ball.",
        "es": "No puedo ayudarte con cuestiones de contrato. Habla con el personal del club o con "
        "tu representante. Sí puedo ayudarte con tu rol y con dónde recibes el balón.",
        "tr": "Sözleşme konularında yardımcı olamam. Lütfen kulüp yetkilileri veya temsilcinle "
        "görüş. Rolün ve topu nerede aldığın konusunda yardımcı olabilirim.",
    },
    Route.TRANSFER: {
        "en": "I can't help with transfer questions. Please speak to club staff or your "
        "representative. I can help with your role and where you receive the ball.",
        "es": "No puedo ayudarte con cuestiones de traspasos. Habla con el personal del club o con "
        "tu representante. Sí puedo ayudarte con tu rol y con dónde recibes el balón.",
        "tr": "Transfer konularında yardımcı olamam. Lütfen kulüp yetkilileri veya temsilcinle "
        "görüş. Rolün ve topu nerede aldığın konusunda yardımcı olabilirim.",
    },
}

_UNVERIFIED: dict[str, str] = {
    "en": "I couldn't back that answer with the match data, so I won't give it. Please ask your "
    "analyst, or try a more specific question.",
    "es": "No he podido respaldar esa respuesta con los datos del partido, así que no la doy. "
    "Consulta a tu analista o haz una pregunta más concreta.",
    "tr": "Bu yanıtı maç verileriyle doğrulayamadım, bu yüzden vermiyorum. Lütfen analistine sor "
    "veya daha net bir soru sor.",
}

_DECLINED: dict[str, str] = {
    "en": "I can't answer that one. Please speak to the staff.",
    "es": "No puedo responder a eso. Habla con el personal del equipo.",
    "tr": "Bu soruyu yanıtlayamam. Lütfen teknik ekiple görüş.",
}


def referral_message(route: Route, language: str) -> str:
    return _REFERRALS[route][language]


def unverified_message(language: str) -> str:
    return _UNVERIFIED[language]


def declined_message(language: str) -> str:
    return _DECLINED[language]
