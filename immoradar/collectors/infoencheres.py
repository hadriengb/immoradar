"""Info-Enchères — annonces de ventes aux enchères immobilières.

Constat du 26/11/2026 : robots.txt autorise tout, MAIS les mentions légales (§3, conditions générales
d'utilisation) réservent le service « à un usage strictement personnel » et précisent que « nul ne peut
copier, reproduire, distribuer, télécharger, ou transmettre les documents et l'information » hors consultation
personnelle ; « toute reproduction […] à d'autres fins […] est interdite ».
=> Par prudence, pas de collecte automatique : lien direct + import manuel (coller l'adresse et le texte de l'annonce).
"""
NAME = "infoencheres"
LABEL = "Info-Enchères"
HOME = "https://www.info-encheres.com"
MODE = "manuel"
LEGAL = ("Collecte automatique non retenue : les conditions d'utilisation limitent le service à un usage strictement "
         "personnel et interdisent de copier ou reproduire l'information. Lien direct + import manuel.")


def manual_links(deps):
    return [{"label": "Info-Enchères — annonces en cours", "url": HOME + "/vente-encheres-immobilieres-annonces.html"}]
