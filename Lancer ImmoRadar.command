#!/bin/bash
# Double-cliquez sur ce fichier pour lancer ImmoRadar (macOS).
# La première fois, si macOS refuse : clic droit > Ouvrir > Ouvrir.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 n'est pas installé. macOS va proposer de l'installer (outils de développement Apple)."
  xcode-select --install
  echo "Une fois l'installation terminée, relancez ce fichier."
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi
echo "Démarrage d'ImmoRadar… (gardez cette fenêtre ouverte ; fermez-la pour arrêter l'application)"
python3 run.py
echo
read -r -p "ImmoRadar s'est arrêté. Appuyez sur Entrée pour fermer cette fenêtre."
