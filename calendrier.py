import re
import time
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Response
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

app = FastAPI()

URL_CLUB_AFRICAIN = "https://www.flashscore.fr/equipe/club-africain/C2lt6Igm/calendrier/"
TZ_TUNIS = ZoneInfo("Africa/Tunis")
DUREE_CACHE = 6 * 3600  # 6 heures

cache = {"ics": None, "ts": 0.0}
verrou = threading.Lock()  # évite de lancer plusieurs Chromium en parallèle


def formater_date(date_texte):
    """Retourne (debut, toute_la_journee).
    '21.11. 17:30' -> datetime UTC ; '02.01.2027' -> date seule (heure inconnue)."""
    try:
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*(?:(\d{1,2}):(\d{2}))?", date_texte)
        if not m:
            raise ValueError("format de date inconnu")

        jour, mois, annee, heure, minute = m.groups()
        maintenant = datetime.now(TZ_TUNIS)
        annee_explicite = annee is not None
        annee = int(annee) if annee_explicite else maintenant.year

        # Heure inconnue : événement sur toute la journée
        if heure is None:
            return datetime(annee, int(mois), int(jour)).date(), True

        d = datetime(annee, int(mois), int(jour), int(heure), int(minute), tzinfo=TZ_TUNIS)
        # Match en janvier alors qu'on est en novembre : année suivante
        if not annee_explicite and d < maintenant - timedelta(days=180):
            d = d.replace(year=annee + 1)
        return d.astimezone(timezone.utc), False
    except ValueError as e:
        print(f"Erreur de formatage de la date : {date_texte} -> {e}")
        return None, False


def nettoyer(nom):
    """Retire les suffixes de pays : 'Zamalek (Egy)' -> 'Zamalek'."""
    return re.sub(r"\s*\([^)]*\)", "", nom).strip()


def extraire_matchs():
    matchs = []
    with sync_playwright() as p:
        navigateur = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            contexte = navigateur.new_context(
                timezone_id="Africa/Tunis",
                locale="fr-FR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            page = contexte.new_page()

            # Bloquer images, polices et médias pour alléger le chargement
            page.route(
                "**/*",
                lambda r: r.abort()
                if r.request.resource_type in ("image", "font", "media")
                else r.continue_(),
            )

            print("Récupération des matchs du Club Africain...")
            page.goto(URL_CLUB_AFRICAIN, wait_until="domcontentloaded", timeout=60000)

            # Bandeau de cookies éventuel
            try:
                page.click("#onetrust-accept-btn-handler", timeout=3000)
            except Exception:
                pass

            try:
                page.wait_for_selector(".event__match", timeout=30000)
            except Exception:
                print("Sélecteur introuvable. Titre :", page.title())
                print(page.content()[:1500])
                raise

            lignes = page.locator(".event__match").all()
            print(f"J'ai trouvé {len(lignes)} matchs prévus.")

            for index, ligne in enumerate(lignes):
                try:
                    date_brute = ligne.locator(".event__stageTime").first.text_content(timeout=2000).strip()
                    domicile = ligne.locator(".event__homeParticipant").first.text_content(timeout=2000).strip()
                    exterieur = ligne.locator(".event__awayParticipant").first.text_content(timeout=2000).strip()
                    print(f"Match {index + 1} : {date_brute} | {domicile} vs {exterieur}")

                    debut, toute_journee = formater_date(date_brute)
                    if debut:
                        matchs.append({
                            "debut": debut,
                            "toute_journee": toute_journee,
                            "domicile": nettoyer(domicile),
                            "exterieur": nettoyer(exterieur),
                        })
                except Exception as e:
                    print(f"Ligne {index + 1} ignorée : {e}")
                    if index == 0:
                        print(ligne.inner_html()[:1500])
                    continue
        finally:
            navigateur.close()
    return matchs


def construire_ics(matchs):
    calendrier = Calendar()
    calendrier.add("prodid", "-//Mon Calendrier Foot//")
    calendrier.add("version", "2.0")
    calendrier.add("x-wr-calname", "Club Africain")
    calendrier.add("x-wr-timezone", "Africa/Tunis")

    for m in matchs:
        ev = Event()
        ev.add("uid", f"{m['debut']:%Y%m%dT%H%M}-{m['domicile']}-{m['exterieur']}@club-africain".replace(" ", ""))
        ev.add("dtstamp", datetime.now(timezone.utc))

        if m["toute_journee"]:
            ev.add("summary", f"🔴⚪ {m['domicile']} vs {m['exterieur']} (heure à confirmer)")
            ev.add("dtstart", m["debut"])
            ev.add("dtend", m["debut"] + timedelta(days=1))
        else:
            ev.add("summary", f"🔴⚪ {m['domicile']} vs {m['exterieur']}")
            ev.add("dtstart", m["debut"])
            ev.add("dtend", m["debut"] + timedelta(hours=2))

        calendrier.add_component(ev)

    return calendrier.to_ical().decode("utf-8")


@app.api_route("/club-africain.ics", methods=["GET", "HEAD"])
def generer_flux():
    # Cache encore valide : réponse immédiate
    if cache["ics"] and time.time() - cache["ts"] < DUREE_CACHE:
        return Response(cache["ics"], media_type="text/calendar; charset=utf-8")

    with verrou:
        # Un autre appel a peut-être rempli le cache pendant l'attente
        if cache["ics"] and time.time() - cache["ts"] < DUREE_CACHE:
            return Response(cache["ics"], media_type="text/calendar; charset=utf-8")

        try:
            matchs = extraire_matchs()
            if not matchs:
                raise RuntimeError("Aucun match trouvé")
            ics = construire_ics(matchs)
            cache["ics"], cache["ts"] = ics, time.time()
            print(f"✅ {len(matchs)} matchs ajoutés au calendrier !")
            return Response(ics, media_type="text/calendar; charset=utf-8")
        except Exception as e:
            print(f"Échec du scraping : {e}")
            # Ancien cache disponible : on le sert plutôt qu'une erreur
            if cache["ics"]:
                return Response(cache["ics"], media_type="text/calendar; charset=utf-8")
            return Response("Service temporaire indisponible", status_code=503)


@app.get("/")
def accueil():
    return {"status": "ok", "calendrier": "/club-africain.ics"}
