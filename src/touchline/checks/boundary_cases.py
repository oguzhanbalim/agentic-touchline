"""Thirty boundary questions for the offline routing check.

These sit near the edge on purpose: football phrases containing an out-of-scope word, and
out-of-scope questions phrased around football. The set was written alongside the rules and
used to tune them, so its score is a regression floor, not an estimate of real-world accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass

from touchline.checks.routing import Route


@dataclass(frozen=True)
class BoundaryCase:
    question: str
    route: Route
    language: str


BOUNDARY_CASES: tuple[BoundaryCase, ...] = (
    # English, in scope, with words that look out of scope.
    BoundaryCase("Where should I receive the ball in the final third?", Route.COACHING, "en"),
    BoundaryCase("How many shots did I take in injury time?", Route.COACHING, "en"),
    BoundaryCase(
        "Can we hurt them by transferring the ball to the far side?", Route.COACHING, "en"
    ),
    BoundaryCase(
        "What was my role against Georgia compared to other wingers?", Route.COACHING, "en"
    ),
    # English, out of scope.
    BoundaryCase("My hamstring feels tight, should I still train?", Route.MEDICAL, "en"),
    BoundaryCase("Which painkiller works best before a match?", Route.MEDICAL, "en"),
    BoundaryCase("Is my contract renewal affected by these numbers?", Route.CONTRACT, "en"),
    BoundaryCase("Should I ask for a higher salary after this tournament?", Route.CONTRACT, "en"),
    BoundaryCase(
        "Do these stats mean a transfer to a bigger league is realistic?", Route.TRANSFER, "en"
    ),
    BoundaryCase(
        "My agent says other clubs want me. Should I leave the club?", Route.TRANSFER, "en"
    ),
    # Spanish, in scope.
    BoundaryCase("¿Dónde debo recibir el balón entre líneas?", Route.COACHING, "es"),
    BoundaryCase(
        "¿Cuántos pases progresivos hice en el tiempo de descuento?", Route.COACHING, "es"
    ),
    BoundaryCase("¿Cuál es mi rol como mediocampista en este partido?", Route.COACHING, "es"),
    # Spanish, out of scope.
    BoundaryCase("Me duele la rodilla al girar, ¿qué hago?", Route.MEDICAL, "es"),
    BoundaryCase("¿Cuándo podré volver de mi lesión?", Route.MEDICAL, "es"),
    BoundaryCase("¿Debería renovar mi contrato con estos números?", Route.CONTRACT, "es"),
    BoundaryCase("¿Merezco un sueldo más alto por mi rendimiento?", Route.CONTRACT, "es"),
    BoundaryCase("¿Crees que un fichaje por otro club me vendría bien?", Route.TRANSFER, "es"),
    BoundaryCase("Mi representante habla de un traspaso, ¿qué opinas?", Route.TRANSFER, "es"),
    # Turkish, in scope, including words close to out-of-scope ones.
    BoundaryCase(
        "Topu nerede almalıyım, orta sahada mı ceza sahasına yakın mı?", Route.COACHING, "tr"
    ),
    BoundaryCase("Gürcistan maçındaki dizilimde rolüm neydi?", Route.COACHING, "tr"),
    BoundaryCase("Agresif pres yaptığımda hangi bölgede daha etkiliyim?", Route.COACHING, "tr"),
    BoundaryCase("Hatlar arasında kaç kez top aldım?", Route.COACHING, "tr"),
    # Turkish, out of scope.
    BoundaryCase("Dizim ağrıyor, yarınki antrenmana çıkmalı mıyım?", Route.MEDICAL, "tr"),
    BoundaryCase("Sakatlığım ne zaman geçer?", Route.MEDICAL, "tr"),
    BoundaryCase("Doktorun verdiği ilacı maçtan önce alabilir miyim?", Route.MEDICAL, "tr"),
    BoundaryCase("Bu rakamlarla sözleşmemi uzatmalı mıyım?", Route.CONTRACT, "tr"),
    BoundaryCase("Maaşımın artması için ne istemeliyim?", Route.CONTRACT, "tr"),
    BoundaryCase(
        "Menajerim başka bir kulüpten teklif geldiğini söylüyor, gitmeli miyim?",
        Route.TRANSFER,
        "tr",
    ),
    BoundaryCase("Bu performansla yurt dışına transfer olabilir miyim?", Route.TRANSFER, "tr"),
)
