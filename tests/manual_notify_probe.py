"""Sonde manuelle : l'enceinte notifie-t-elle les changements faits sur ses
molettes physiques ?

Lancer, puis tourner les molettes BASS, TREBLE et VOLUME sur l'enceinte.

Subtilite : StartNotify provoque l'envoi immediat de la valeur courante. Ces
notifications d'amorcage ne sont PAS des changements physiques, on les ignore
pendant les premieres secondes pour ne pas conclure a tort.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gi.repository import GLib
from marshall_ble import Speaker

AMORCAGE_S = 4
ECOUTE_S = 90

spk = Speaker()
if not spk.connect(timeout_s=30):
    sys.exit("enceinte indisponible")

etat_initial = spk.get_state()
print(f"Etat initial : {etat_initial}\n")

debut = time.monotonic()
amorcage = []
reels = []


def on_state(state):
    ecoule = time.monotonic() - debut
    ts = time.strftime("%H:%M:%S")
    if ecoule < AMORCAGE_S:
        amorcage.append(state)
        print(f"[{ts}] (amorcage, ignore) {state}")
    else:
        reels.append(state)
        print(f"[{ts}] *** CHANGEMENT PHYSIQUE DETECTE *** {state}")


spk.subscribe(on_state)
print(f"Abonne. Pendant {ECOUTE_S} s : TOURNE LES MOLETTES de l'enceinte")
print("(bass, treble, puis volume). Chaque changement detecte s'affiche.\n")

loop = GLib.MainLoop()
GLib.timeout_add_seconds(ECOUTE_S, lambda: (loop.quit(), False)[1])
try:
    loop.run()
except KeyboardInterrupt:
    pass

print(f"\n{'=' * 58}")
print(f"notifications d'amorcage ignorees : {len(amorcage)}")
print(f"changements physiques detectes    : {len(reels)}")
if reels:
    print("\n>>> REFLET PHYSIQUE REALISABLE — le plan continue tel quel.")
else:
    print("\n>>> AUCUNE notification de changement physique.")
    print(">>> Si les molettes ONT ete tournees : retirer le reflet en direct")
    print(">>>   du perimetre (le reste du plan est inchange).")
    print(">>> Si elles n'ont PAS ete tournees : relancer la sonde.")
spk.close()
