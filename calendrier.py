from playwright.sync_api import sync_playwright
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from icalendar import Calendar, Event
from datetime import timedelta, datetime

app = FastAPI()
URL_CLUB_AFRICAIN = "https://www.flashscore.fr/equipe/club-africain/C2lt6Igm/calendrier/"

def formater_date(date_texte):
    try:
        annee = datetime.now().year
        date_complete = f"{date_texte.strip()} {annee}"
        return datetime.strptime(date_complete, "%d.%m. %H:%M %Y")
    except ValueError as e:
        print(f"Erreur de formatage de la date :{date_texte} -> {e}")
        return None

def extraire_matchs():
    matchs = []
    with sync_playwright() as p:
        navigateur = p.chromium.launch(headless=True)
        page = navigateur.new_page()
        print("Récupération des matchs du Club Africain...")
        page.goto(URL_CLUB_AFRICAIN)
        page.wait_for_selector(".event__match", timeout=15000)
        lignes = page.locator(".event__match").all()
        print(f"J'ai trouvé {len(lignes)} matchs prévus.")
        for index, ligne in enumerate(lignes):
            try:
                date_brute = ligne.locator(".event__time").text_content(timeout=2000).strip()
                domicile = ligne.locator(".event__homeParticipant").text_content(timeout=2000).strip()
                exterieur = ligne.locator(".event__awayParticipant").text_content(timeout=2000).strip()
                print(f"Match {index+1} : {date_brute} | {domicile} vs {exterieur}")
                date_formatee = formater_date(date_brute)
                if date_formatee:
                    matchs.append({
                        "debut": date_formatee,
                        "domicile": domicile,
                        "exterieur": exterieur
                    })
            except Exception as e:
                continue
                
        navigateur.close()
    return matchs

@app.get("/club-africain.ics", response_class=PlainTextResponse)
def generer_flux():
    calendrier = Calendar()
    calendrier.add('prodid', '-//Mon Calendrier Foot//')
    calendrier.add('version', '2.0')
    matchs = extraire_matchs()
    for match in matchs:
        evenement = Event()
        evenement.add('summary', f"🔴⚪ {match['domicile']} vs {match['exterieur']}")
        evenement.add('dtstart', match['debut'])
        heure_fin = match['debut'] + timedelta(hours=2)
        evenement.add('dtend', heure_fin)
        calendrier.add_component(evenement)
    print(f"✅{len(matchs)} matchs ajoutés au calendrier !")
    return calendrier.to_ical().decode("utf-8")
