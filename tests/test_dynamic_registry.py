from arb.teams import DynamicRegistry


def test_tennis_names_cluster_across_venues():
    reg = DynamicRegistry("itf_m", ["Novak Djokovic", "Djokovic", "N. Djokovic", "Jannik Sinner", "J Sinner",
                                    "Carlos Alcaraz", "Alcaraz"])
    assert reg.resolve("Djokovic") == reg.resolve("Novak Djokovic") == reg.resolve("N Djokovic")
    assert reg.resolve("Sinner") == reg.resolve("Jannik Sinner")
    assert reg.resolve("Alcaraz") != reg.resolve("Sinner")
    assert len(reg.teams) == 3


def test_club_names_ignore_generic_tokens():
    reg = DynamicRegistry("liga_portugal", ["Sporting CP", "Sporting Braga", "FC Porto", "Porto", "SL Benfica", "Benfica",
                                            "Cagliari", "Cagliari Calcio", "US Lecce", "Lecce"])
    assert reg.resolve("Sporting CP") != reg.resolve("Sporting Braga")  # 'sporting' alone must not merge them
    assert reg.resolve("Porto") == reg.resolve("FC Porto")
    assert reg.resolve("Benfica") == reg.resolve("SL Benfica")
    assert reg.resolve("Cagliari Calcio") == reg.resolve("Cagliari")
    assert reg.resolve("US Lecce") == reg.resolve("Lecce")
    assert reg.resolve("Sporting") is None or reg.resolve("Sporting") not in (reg.resolve("Sporting CP"),)


def test_same_city_different_clubs_stay_apart():
    reg = DynamicRegistry("laliga", ["Real Madrid", "Atletico Madrid", "Manchester United", "Manchester City",
                                     "Inter Milan", "AC Milan"])
    assert reg.resolve("Real Madrid") != reg.resolve("Atletico Madrid")
    assert reg.resolve("Manchester United") != reg.resolve("Manchester City")
    assert reg.resolve("Inter Milan") != reg.resolve("AC Milan")
    assert reg.espn_team_id({"displayName": "Real Madrid", "shortDisplayName": "Real Madrid"}) == reg.resolve("Real Madrid")
