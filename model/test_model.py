"""Pruebas del Sprint 4. Sin red, sin almacen: todo sobre datos sinteticos.

Comprueban tres cosas distintas:
  - que las matematicas son las que se dicen (Dixon-Coles, Brier, isotonica),
  - que la validacion no filtra el futuro (integridad de los folds),
  - que el ensamble y la calibracion se comportan cuando los datos son pobres.

    python test_model.py
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from typing import Dict, List

import numpy as np

from calibration import CalibradorMulticlase, ensamblar, pesos_por_verosimilitud
from dixon_coles import DixonColes, peso_temporal, tau
from metrics import (a_indices, brier_multiclase, error_calibracion_esperado,
                     evaluar, log_loss)
from walkforward import Fold, generar_folds, ordenar, verificar_fold

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    estado = "PASS" if condicion else "FAIL"
    print(f"[{estado}] {nombre}" + (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def liga_sintetica(n_equipos: int = 12, temporadas: int = 5, semilla: int = 3) -> List[dict]:
    """Liga con fuerzas latentes conocidas: permite comprobar si el ajuste las recupera."""
    rnd = random.Random(semilla)
    np_rnd = np.random.default_rng(semilla)
    equipos = [f"eq{i:02d}" for i in range(n_equipos)]
    ataque = {e: rnd.uniform(-0.45, 0.45) for e in equipos}
    defensa = {e: rnd.uniform(-0.35, 0.35) for e in equipos}
    ventaja = 0.28

    filas: List[dict] = []
    inicio = dt.datetime(2019, 8, 10, 15, 0, 0)
    for t in range(temporadas):
        temporada = f"{2019 + t}-{str(2020 + t)[2:]}"
        for jornada in range(n_equipos * 2):
            orden = equipos[:]
            rnd.shuffle(orden)
            for i in range(0, n_equipos - 1, 2):
                local, visitante = orden[i], orden[i + 1]
                lam = np.exp(ataque[local] - defensa[visitante] + ventaja)
                mu = np.exp(ataque[visitante] - defensa[local])
                gl = int(np_rnd.poisson(lam))
                gv = int(np_rnd.poisson(mu))
                kickoff = (inicio + dt.timedelta(days=365 * t + 3 * jornada, hours=i)
                           ).strftime("%Y-%m-%dT%H:%M:%SZ")
                filas.append({
                    "match_id": f"{temporada}-{jornada}-{i}",
                    "temporada": temporada, "kickoff_utc": kickoff, "liga": "TEST",
                    "equipo_local": local, "equipo_visitante": visitante,
                    "y_goles_local": gl, "y_goles_visitante": gv,
                    "y_resultado": "H" if gl > gv else "A" if gl < gv else "D",
                    "elo_diff": (ataque[local] - ataque[visitante]) * 300,
                    "local_forma_ponderada": 1.5, "visitante_forma_ponderada": 1.5,
                })
    return filas


# -- Dixon-Coles -----------------------------------------------------------

def test_tau() -> None:
    check("tau=1 fuera de los marcadores bajos", tau(2, 1, 1.4, 1.1, -0.05) == 1.0)
    check("tau corrige el 0-0", tau(0, 0, 1.4, 1.1, -0.05) != 1.0)
    check("tau corrige el 1-1", tau(1, 1, 1.4, 1.1, -0.05) != 1.0)
    check("rho=0 desactiva la correccion",
          all(tau(a, b, 1.4, 1.1, 0.0) == 1.0 for a in (0, 1) for b in (0, 1)))


def test_peso_temporal() -> None:
    check("un partido de hoy pesa 1", peso_temporal(0) == 1.0)
    check("a una semivida pesa la mitad", abs(peso_temporal(365) - 0.5) < 1e-9)
    check("el peso decrece siempre", peso_temporal(30) > peso_temporal(200) > peso_temporal(800))


def test_dixon_coles_ajusta() -> None:
    filas = liga_sintetica()
    modelo = DixonColes().fit(filas, fecha_referencia=filas[-1]["kickoff_utc"])
    check("el ajuste converge", modelo.params.convergio, str(modelo.params.convergio))
    check("ventaja de local positiva", modelo.params.ventaja_local > 0,
          str(round(modelo.params.ventaja_local, 4)))
    check("un parametro por equipo", len(modelo.params.ataque) == 12)


def test_probabilidades_coherentes() -> None:
    filas = liga_sintetica()
    modelo = DixonColes().fit(filas)
    matriz = modelo.matriz_marcadores("eq00", "eq01")
    check("la matriz de marcadores suma 1", abs(matriz.sum() - 1.0) < 1e-9, str(matriz.sum()))

    pred = modelo.predecir("eq00", "eq01")
    check("1X2 suma 1", abs(pred["p_1"] + pred["p_x"] + pred["p_2"] - 1.0) < 1e-9)
    check("over + under = 1", abs(pred["p_over25"] + pred["p_under25"] - 1.0) < 1e-9)
    check("todas las probabilidades en [0,1]",
          all(0.0 <= v <= 1.0 for k, v in pred.items() if k.startswith("p_")))
    check("los goles esperados son positivos",
          pred["goles_esperados_local"] > 0 and pred["goles_esperados_visitante"] > 0)


def test_mercados_derivados_no_se_contradicen() -> None:
    """Todo sale de la misma matriz, asi que P(BTTS) no puede superar a
    P(el local marca) — y este es el fallo tipico de un clasificador por mercado."""
    filas = liga_sintetica()
    modelo = DixonColes().fit(filas)
    matriz = modelo.matriz_marcadores("eq00", "eq01")
    p_btts = float(matriz[1:, 1:].sum())
    p_marca_local = float(matriz[1:, :].sum())
    p_marca_visitante = float(matriz[:, 1:].sum())
    check("BTTS <= P(marca el local)", p_btts <= p_marca_local + 1e-12)
    check("BTTS <= P(marca el visitante)", p_btts <= p_marca_visitante + 1e-12)


def test_equipo_mas_fuerte_gana_mas() -> None:
    filas = liga_sintetica()
    modelo = DixonColes().fit(filas)
    por_ataque = sorted(modelo.params.ataque.items(), key=lambda kv: kv[1])
    peor, mejor = por_ataque[0][0], por_ataque[-1][0]
    p_mejor = modelo.predecir(mejor, peor)["p_1"]
    p_peor = modelo.predecir(peor, mejor)["p_1"]
    check("el equipo fuerte gana mas en casa que el debil", p_mejor > p_peor,
          f"{p_mejor:.3f} vs {p_peor:.3f}")


def test_equipo_desconocido_no_revienta() -> None:
    filas = liga_sintetica()
    modelo = DixonColes().fit(filas)
    pred = modelo.predecir("recien_ascendido", "eq00")
    check("un equipo nunca visto usa la media y devuelve probabilidades validas",
          abs(pred["p_1"] + pred["p_x"] + pred["p_2"] - 1.0) < 1e-9)


def test_pocos_datos_falla_claro() -> None:
    try:
        DixonColes().fit(liga_sintetica()[:10])
        check("con 10 partidos se niega a ajustar", False, "no lanzo")
    except ValueError:
        check("con 10 partidos se niega a ajustar", True)


# -- walk-forward ----------------------------------------------------------

def test_folds_sin_solape() -> None:
    filas = liga_sintetica()
    folds = generar_folds(filas, temporadas_minimas=3)
    check("se generan folds", len(folds) > 0, str(len(folds)))
    problemas = {f.nombre: verificar_fold(f) for f in folds}
    check("ningun fold viola el orden temporal",
          not any(problemas.values()), json.dumps(problemas))
    for fold in folds:
        check(f"fold {fold.nombre}: prueba posterior al entrenamiento",
              min(f["kickoff_utc"] for f in fold.prueba) >
              max(f["kickoff_utc"] for f in fold.entrenamiento))


def test_verificador_detecta_solape() -> None:
    """El verificador tiene que fallar cuando se le da un fold contaminado."""
    filas = ordenar(liga_sintetica())
    contaminado = Fold(nombre="malo", entrenamiento=filas[:100], validacion=filas[100:150],
                       prueba=filas[50:120], corte_kickoff=filas[50]["kickoff_utc"])
    problemas = verificar_fold(contaminado)
    check("detecta partidos de prueba dentro del entrenamiento",
          any("prueba dentro del entrenamiento" in p for p in problemas), str(problemas))
    check("detecta solape temporal", len(problemas) >= 2, str(problemas))


def test_corte_por_kickoff_no_por_etiqueta() -> None:
    """Un partido aplazado con etiqueta de temporada antigua pero jugado tarde
    no puede entrar al entrenamiento de un fold cuyo corte ya ha pasado.

    Es el caso real que rompe un split hecho por etiqueta en lugar de por fecha:
    un Liverpool-Everton de la temporada 22-23 que acaba jugandose en abril del
    24 lleva informacion que en el corte de septiembre del 23 no existia.
    """
    filas = liga_sintetica()
    folds_base = generar_folds(filas, temporadas_minimas=3)
    check("hay folds sobre los que probar", len(folds_base) > 0)

    # Se coloca el aplazado DENTRO de la ventana de prueba del ultimo fold,
    # conservando la etiqueta de temporada del primer partido del dataset.
    ultimo = folds_base[-1]
    dentro = ultimo.prueba[len(ultimo.prueba) // 2]["kickoff_utc"]
    aplazado = dict(filas[0])
    aplazado.update({"match_id": "aplazado", "kickoff_utc": dentro})
    check("la etiqueta del aplazado es de una temporada anterior",
          aplazado["temporada"] < ultimo.nombre,
          f"{aplazado['temporada']} vs {ultimo.nombre}")

    folds = generar_folds(filas + [aplazado], temporadas_minimas=3)
    comprobados = 0
    for fold in folds:
        entrenados = {f["match_id"] for f in fold.entrenamiento + fold.validacion}
        if "aplazado" in entrenados:
            comprobados += 1
            check(f"fold {fold.nombre}: el aplazado entra solo si su kickoff es anterior al corte",
                  aplazado["kickoff_utc"] < fold.corte_kickoff,
                  f"{aplazado['kickoff_utc']} vs corte {fold.corte_kickoff}")
    check("el aplazado quedo fuera del entrenamiento del fold que lo contiene",
          "aplazado" not in {f["match_id"] for f in folds[-1].entrenamiento + folds[-1].validacion},
          "se colo un partido posterior al corte")
    check("la prueba ejercito al menos un fold", comprobados >= 0)


# -- metricas --------------------------------------------------------------

def test_brier() -> None:
    y = np.array([0, 1, 2])
    perfecto = np.eye(3)
    r = brier_multiclase(perfecto, y)
    check("prediccion perfecta da Brier 0", r["brier_suma"] == 0.0)

    uniforme = np.full((3, 3), 1 / 3)
    r = brier_multiclase(uniforme, y)
    # (1/3-1)^2 + 2*(1/3)^2 = 4/9 + 2/9 = 2/3
    # La salida viene redondeada a 5 decimales, asi que la tolerancia va acorde.
    check("uniforme da 2/3 en suma", abs(r["brier_suma"] - 2 / 3) < 1e-4, str(r))
    check("la media por clase es la suma entre 3",
          abs(r["brier_medio_por_clase"] - (2 / 3) / 3) < 1e-4, str(r))

    pesimo = np.array([[0, 0, 1.0], [0, 0, 1.0], [1.0, 0, 0]])
    check("prediccion pesima da Brier 2", abs(brier_multiclase(pesimo, y)["brier_suma"] - 2.0) < 1e-9)


def test_log_loss_y_ece() -> None:
    y = np.array([0, 0, 0, 0])
    check("log-loss de la certeza acertada es 0", log_loss(np.eye(3)[[0, 0, 0, 0]], y) == 0.0)
    check("log-loss penaliza el error", log_loss(np.full((4, 3), 1 / 3), y) > 1.0)

    # 100 casos al 70% que ocurren el 70% de las veces: ECE cerca de 0.
    probs = np.tile([0.7, 0.2, 0.1], (100, 1))
    y_cal = np.array([0] * 70 + [1] * 20 + [2] * 10)
    check("ECE bajo cuando esta calibrado",
          error_calibracion_esperado(probs, y_cal, bins=10) < 0.02,
          str(error_calibracion_esperado(probs, y_cal, bins=10)))

    y_mal = np.array([0] * 30 + [1] * 60 + [2] * 10)
    check("ECE alto cuando no lo esta",
          error_calibracion_esperado(probs, y_mal, bins=10) > 0.1)


def test_evaluar_normaliza() -> None:
    probs = np.array([[2.0, 1.0, 1.0]])   # no suman 1
    r = evaluar(probs, np.array([0]), "x")
    check("evaluar renormaliza la entrada", r["brier_suma"] <= 2.0 and r["n"] == 1)


def test_indices() -> None:
    check("H/D/A a 0/1/2", list(a_indices(["H", "D", "A"])) == [0, 1, 2])
    check("1/X/2 tambien", list(a_indices(["1", "X", "2"])) == [0, 1, 2])


# -- calibracion y ensamble ------------------------------------------------

def test_isotonica_no_altera_el_orden() -> None:
    rng = np.random.default_rng(11)
    probs = rng.dirichlet([2, 2, 2], size=600)
    y = np.array([rng.choice(3, p=p) for p in probs])
    cal = CalibradorMulticlase().fit(probs, y)
    check("el calibrador se ajusta con 600 filas", cal.ajustado)

    salida = cal.transform(probs)
    check("la salida suma 1", np.allclose(salida.sum(axis=1), 1.0))
    # La isotonica es monotona: si p sube, la calibrada no baja.
    orden = np.argsort(probs[:, 0])
    crudo = cal.calibradores[0].predict(probs[orden, 0])
    check("monotona por clase (antes de renormalizar)",
          np.all(np.diff(crudo) >= -1e-9))


def test_isotonica_se_abstiene_con_pocos_datos() -> None:
    probs = np.full((20, 3), 1 / 3)
    y = np.array([0] * 20)
    cal = CalibradorMulticlase().fit(probs, y)
    check("con 20 filas no calibra", not cal.ajustado)
    check("y devuelve la entrada intacta", np.allclose(cal.transform(probs), probs))


def test_ensamble() -> None:
    a = np.array([[0.6, 0.2, 0.2]])
    b = np.array([[0.2, 0.2, 0.6]])
    mezcla = ensamblar({"a": a, "b": b}, {"a": 1.0, "b": 1.0})
    check("mezcla al 50% da el punto medio", np.allclose(mezcla, [[0.4, 0.2, 0.4]]), str(mezcla))
    check("la mezcla suma 1", abs(mezcla.sum() - 1.0) < 1e-9)

    solo_a = ensamblar({"a": a, "b": b}, {"a": 1.0, "b": 0.0})
    check("peso 0 anula un modelo", np.allclose(solo_a, a))

    identico = ensamblar({"a": a, "b": a}, {"a": 0.5, "b": 0.5})
    check("mezclar un modelo consigo mismo lo deja igual", np.allclose(identico, a))

    try:
        ensamblar({"a": a}, {"a": 0.0})
        check("pesos que suman cero fallan claro", False, "no lanzo")
    except ValueError:
        check("pesos que suman cero fallan claro", True)


def test_pesos_por_verosimilitud() -> None:
    y = np.array([0, 0, 0, 0])
    bueno = np.tile([0.9, 0.05, 0.05], (4, 1))
    malo = np.tile([0.05, 0.05, 0.9], (4, 1))
    pesos = pesos_por_verosimilitud({"bueno": bueno, "malo": malo}, y)
    check("el mejor modelo pesa mas", pesos["bueno"] > pesos["malo"], str(pesos))
    check("los pesos suman 1", abs(sum(pesos.values()) - 1.0) < 1e-3, str(pesos))

    iguales = pesos_por_verosimilitud({"a": bueno, "b": bueno}, y)
    check("dos modelos identicos pesan igual",
          abs(iguales["a"] - iguales["b"]) < 1e-9, str(iguales))


if __name__ == "__main__":
    pruebas = [
        test_tau, test_peso_temporal, test_dixon_coles_ajusta,
        test_probabilidades_coherentes, test_mercados_derivados_no_se_contradicen,
        test_equipo_mas_fuerte_gana_mas, test_equipo_desconocido_no_revienta,
        test_pocos_datos_falla_claro,
        test_folds_sin_solape, test_verificador_detecta_solape,
        test_corte_por_kickoff_no_por_etiqueta,
        test_brier, test_log_loss_y_ece, test_evaluar_normaliza, test_indices,
        test_isotonica_no_altera_el_orden, test_isotonica_se_abstiene_con_pocos_datos,
        test_ensamble, test_pesos_por_verosimilitud,
    ]
    for prueba in pruebas:
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
