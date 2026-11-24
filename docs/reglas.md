# Reglas de negocio → pruebas

Ésta es la compuerta de calidad de Acuse (más que el % de cobertura): cada regla del dominio tiene al menos una
prueba que falla si la regla se rompe. Las referencias son `archivo::prueba` desde la raíz del repo.
`tests/test_reglas_doc.py` (corre en el CI) verifica que cada prueba citada exista y no esté marcada para saltarse,
que ninguna regla se quede sin prueba y que **ninguna prueba quede sin regla**.

La persistencia real es SQLite (en memoria por omisión, en archivo con `ACUSE_BD`): las pruebas de integración abren
un archivo de verdad, no un contenedor ([ADR 0006](adr/0006-sqlite-como-persistencia.md)). Para agregar una regla:
escribe la prueba primero, agrégala a la tabla y corre `pytest`.

## 1. Estados de la entrega · `acuse/modelo.py`, `acuse/estados.py`

| # | Regla | Pruebas |
|---|---|---|
| E1 | Sólo ACUSADO cuenta como entregado; ENVIADO no | `tests/test_maquina_estados.py::test_solo_acusado_cuenta_como_entregado`, `tests/test_orquestador.py::test_canal_asincrono_deja_enviado_y_no_entregado` |
| E2 | ENVIADO es trabajo abierto (con PENDIENTE); SIN_ACUSE, FALLIDO y RECHAZADO son problemas con nombre | `tests/test_maquina_estados.py::test_enviado_es_trabajo_abierto_y_los_problemas_tienen_nombre` |
| E3 | La tabla (estado, evento) → estado del código es exactamente la del negocio: 18 transiciones válidas | `tests/test_maquina_estados.py::test_la_tabla_del_codigo_es_exactamente_la_del_negocio`, `tests/test_maquina_estados.py::test_transicion_valida` |
| E4 | Las 18 transiciones inválidas lanzan `TransicionInvalida` con estado, evento y los eventos válidos | `tests/test_maquina_estados.py::test_transicion_invalida_se_rechaza_con_motivo` |
| E5 | ACUSADO es terminal, nadie vuelve a PENDIENTE y todo estado es alcanzable | `tests/test_maquina_estados.py::test_acusado_es_el_unico_estado_terminal`, `tests/test_maquina_estados.py::test_nadie_vuelve_a_pendiente_y_todo_estado_es_alcanzable` |
| E6 | Sólo se acusa lo ENVIADO; sobre SIN_ACUSE queda `acuse_tardio` (el incumplimiento no se borra), sobre el resto `acuse_ignorado` | `tests/test_maquina_estados.py::test_el_acuse_del_receptor_solo_mueve_lo_que_esta_enviado`, `tests/test_orquestador.py::test_registrar_acuse_pasa_enviado_a_acusado` |
| E7 | Sólo vence lo ENVIADO | `tests/test_maquina_estados.py::test_el_vencimiento_solo_mueve_lo_que_esta_enviado`, `tests/test_bordes_fechas.py::test_solo_una_entrega_enviado_puede_vencer` |
| E8 | Lo vivo (ENVIADO, ACUSADO) no se reenvía; lo despachable (PENDIENTE, FALLIDO, RECHAZADO, SIN_ACUSE) se reintenta sobre la MISMA entrega | `tests/test_maquina_estados.py::test_enviado_y_acusado_no_se_despachan_de_nuevo`, `tests/test_maquina_estados.py::test_lo_vivo_no_se_reenvia`, `tests/test_maquina_estados.py::test_lo_despachable_se_reintenta_sobre_la_misma_entrega`, `tests/test_orquestador.py::test_reintento_de_una_fallida_reusa_la_misma_entrega` |
| E9 | Hay brecha sólo cuando algo excedió su plazo | `tests/test_maquina_estados.py::test_hay_brecha_solo_cuando_algo_excedio_su_plazo` |

## 2. Fechas y zonas horarias · `acuse.modelo.a_utc`, `Entrega.vencida`

| # | Regla | Pruebas |
|---|---|---|
| T1 | El plazo se mide en tiempo absoluto (UTC), también la madrugada del cambio de horario (defecto corregido en la semana 3) | `tests/test_bordes_fechas.py::test_el_plazo_se_mide_en_tiempo_absoluto_aun_con_cambio_de_horario`, `tests/test_bordes_fechas.py::test_el_conciliador_marca_vencidas_en_tiempo_absoluto_con_reloj_local` |
| T2 | Vence sólo con MÁS del plazo transcurrido; en el límite exacto, no | `tests/test_bordes_fechas.py::test_el_limite_del_plazo_es_estricto`, `tests/test_conciliador.py::test_dentro_del_plazo_no_se_marca_nada` |
| T3 | Sin fecha de envío no hay vencimiento | `tests/test_bordes_fechas.py::test_sin_fecha_de_envio_no_vence` |
| T4 | Una fecha sin zona no se adivina: es un error en el dominio y en el conciliador | `tests/test_bordes_fechas.py::test_una_fecha_sin_zona_no_se_adivina`, `tests/test_bordes_fechas.py::test_el_conciliador_rechaza_un_momento_sin_zona` |
| T5 | Pasar a UTC conserva el instante | `tests/test_bordes_fechas.py::test_a_utc_conserva_el_instante` |

## 3. Ruteo explícito · `acuse/ruteo.py` ([ADR 0003](adr/0003-ruteo-explicito.md))

| # | Regla | Pruebas |
|---|---|---|
| R1 | Gana la regla de menor prioridad, sin importar el orden declarado | `tests/test_ruteo.py::test_gana_la_menor_prioridad_sin_importar_el_orden_declarado` |
| R2 | Con la misma prioridad y condiciones distintas decide el orden declarado | `tests/test_ruteo.py::test_con_la_misma_prioridad_y_condiciones_distintas_decide_el_orden_declarado` |
| R3 | Misma condición y misma prioridad es un error al construir el enrutador; con prioridad distinta no | `tests/test_ruteo.py::test_dos_reglas_con_la_misma_condicion_y_prioridad_son_un_error_al_construir`, `tests/test_ruteo.py::test_la_misma_condicion_con_prioridad_distinta_no_es_ambigua` |
| R4 | Las condiciones casan por igualdad exacta; un atributo ausente no cae en un canal cualquiera | `tests/test_ruteo.py::test_las_condiciones_casan_por_igualdad_exacta`, `tests/test_orquestador.py::test_atributo_faltante_no_cae_en_un_canal_cualquiera` |
| R5 | Una condición de varios atributos exige todos | `tests/test_ruteo.py::test_una_condicion_de_varios_atributos_exige_todos` |
| R6 | Una regla sin condición es legal sólo como elección explícita: con nombre, prioridad y en la explicación | `tests/test_ruteo.py::test_una_regla_sin_condicion_es_legal_explicita_y_se_explica` |
| R7 | Toda decisión explica qué regla casó, con qué prioridad y qué atributos | `tests/test_ruteo.py::test_la_explicacion_dice_que_regla_con_que_prioridad_y_que_atributos` |
| R8 | Sin ruta no hay canal por omisión: `SinRutaAplicable` con el diagnóstico completo y la entrega RECHAZADO con canal nulo | `tests/test_ruteo.py::test_sin_ruta_el_error_carga_el_diagnostico_completo`, `tests/test_orquestador.py::test_trabajo_sin_regla_queda_rechazado_con_motivo`, `tests/test_api.py::test_los_trabajos_sin_ruta_quedan_rechazados_y_sin_canal` |

## 4. Canales · `acuse/canales/`

| # | Regla | Pruebas |
|---|---|---|
| C1 | Un acuse exige actor y envío aceptado; un rechazo exige motivo | `tests/test_canales.py::test_un_acuse_sin_actor_no_es_un_acuse`, `tests/test_canales_bordes.py::test_un_resultado_imposible_no_se_puede_construir` |
| C2 | Un canal asíncrono no puede declarar acuse inmediato: se le rechaza el acuse, no el envío | `tests/test_canales.py::test_un_canal_asincrono_no_puede_declarar_acuse_inmediato`, `tests/test_canales.py::test_enviar_por_valida_el_invariante_del_acuse`, `tests/test_orquestador.py::test_a_un_canal_asincrono_que_dice_haber_acusado_se_le_rechaza_el_acuse` |
| C3 | El registro valida el contrato (nombre, modo, plazo entero positivo, `enviar`) antes de que alguien dependa del canal | `tests/test_canales_bordes.py::test_registrar_valida_el_contrato_antes_de_que_alguien_dependa_del_canal` |
| C4 | Nada se sustituye en silencio: un duplicado falla salvo `reemplazar=True` y un canal no registrado falla con la lista de los que hay | `tests/test_canales.py::test_registro_da_de_alta_y_resuelve_por_nombre`, `tests/test_canales_bordes.py::test_reemplazar_un_canal_solo_es_posible_si_se_pide`, `tests/test_canales_bordes.py::test_pedir_un_canal_no_registrado_dice_cuales_hay` |
| C5 | Los canales no se construyen con parámetros imposibles (tasa fuera de [0, 1], plazo ≤ 0) | `tests/test_canales_bordes.py::test_los_canales_no_se_construyen_con_parametros_imposibles` |
| C6 | Síncrono: acusa en la llamada con actor; receptor caído o rechazo de contenido no se aceptan y dicen por qué; el rechazo es determinista | `tests/test_canales.py::test_sincrono_acusa_de_inmediato`, `tests/test_canales.py::test_sincrono_receptor_caido_no_acepta`, `tests/test_canales.py::test_sincrono_rechazo_es_determinista_sin_azar`, `tests/test_canales.py::test_sincrono_rechazo_total_no_acepta_nada` |
| C7 | Asíncrono: depositar siempre se acepta y nunca acusa; lo depositado queda pendiente hasta que alguien lo lea | `tests/test_canales.py::test_asincrono_nunca_acusa_al_enviar`, `tests/test_canales.py::test_asincrono_lo_depositado_queda_pendiente_hasta_consumir`, `tests/test_canales.py::test_asincrono_sin_consumidor_la_pila_crece_en_silencio` |
| C8 | Consumir es FIFO, por partes y no re-acusa; `n` negativo es un error y un buzón vacío no inventa nada | `tests/test_canales.py::test_asincrono_consumir_devuelve_las_referencias_en_orden_y_por_partes`, `tests/test_canales_bordes.py::test_consumir_cero_o_negativo_en_el_asincrono`, `tests/test_canales_bordes.py::test_consumir_un_buzon_vacio_no_inventa_nada` |
| C9 | Por lote: un objeto por lote, lotes separados y cada referencia una sola vez | `tests/test_canales.py::test_lote_agrupa_tres_trabajos_en_un_solo_objeto`, `tests/test_canales.py::test_lote_separa_lotes_distintos`, `tests/test_canales_bordes.py::test_la_misma_referencia_dos_veces_en_un_lote_viaja_una_sola_vez` |
| C10 | Sin lote declarado el envío se rechaza: no se inventa un lote (la llave del lote es configurable y explícita) | `tests/test_canales.py::test_lote_sin_lote_se_rechaza_en_vez_de_inventar_uno`, `tests/test_canales_bordes.py::test_un_lote_vacio_o_que_no_es_texto_se_rechaza_en_voz_alta`, `tests/test_canales_bordes.py::test_el_lote_puede_viajar_en_otra_llave_declarada` |
| C11 | Consumir un lote acusa todas sus referencias una sola vez; un lote consumido no se reabre; uno desconocido es error; leer no altera | `tests/test_canales.py::test_lote_consumir_lote_acusa_todas_sus_referencias`, `tests/test_canales.py::test_lote_ya_consumido_no_se_reabre_en_silencio`, `tests/test_cifras_control.py::test_leer_los_registros_del_lote_no_lo_altera_ni_inventa_lotes`, `tests/test_canales_bordes.py::test_leer_los_registros_devuelve_copias` |

## 5. Orquestador · `acuse/orquestador.py`

| # | Regla | Pruebas |
|---|---|---|
| O1 | Síncrono aceptado → ACUSADO con actor y hora del acuse | `tests/test_orquestador.py::test_canal_sincrono_deja_acusado` |
| O2 | Asíncrono aceptado → ENVIADO: aceptado por el canal no es recibido | `tests/test_orquestador.py::test_canal_asincrono_deja_enviado_y_no_entregado` |
| O3 | Un canal no registrado, que revienta o que no acepta deja FALLIDO con motivo y sin actor | `tests/test_orquestador.py::test_canal_resuelto_pero_no_registrado_es_fallido`, `tests/test_orquestador.py::test_canal_que_revienta_deja_fallido_no_hueco`, `tests/test_orquestador.py::test_canal_que_no_acepta_deja_fallido`, `tests/test_orquestador_bordes.py::test_un_canal_sincrono_que_no_acepta_deja_fallido_sin_actor` |
| O4 | Cada canal impone su plazo de acuse; si no es un entero positivo rige el del orquestador | `tests/test_orquestador_bordes.py::test_cada_canal_impone_su_plazo_y_si_no_es_valido_rige_el_del_orquestador` |
| O5 | Una corrida sin insumo es SIN_INSUMO, nunca EXITOSA | `tests/test_orquestador.py::test_lista_vacia_es_sin_insumo_no_exitosa`, `tests/test_api.py::test_corrida_vacia_produce_una_corrida_sin_insumo`, `tests/test_api_bordes.py::test_corrida_con_cero_trabajos_es_sin_insumo` |
| O6 | EXITOSA sólo con algo procesado y sin problemas; CON_PROBLEMAS con rechazados o fallidos; sólo duplicados es SIN_INSUMO | `tests/test_orquestador_bordes.py::test_el_desenlace_de_la_corrida`, `tests/test_orquestador.py::test_procesar_dos_veces_no_duplica`, `tests/test_orquestador_bordes.py::test_una_corrida_exitosa_con_duplicados_lo_dice_en_su_detalle` |
| O7 | Una corrida que revienta queda FALLIDA, se revierte completa y el error no se traga | `tests/test_orquestador.py::test_una_corrida_que_revienta_deja_rastro_y_no_se_traga_el_error` |
| O8 | Sin actor no hay acuse; el actor se guarda sin espacios alrededor | `tests/test_orquestador.py::test_registrar_acuse_sin_actor_lanza_valueerror`, `tests/test_orquestador_bordes.py::test_sin_actor_no_hay_acuse_aunque_la_lista_este_vacia`, `tests/test_orquestador_bordes.py::test_el_actor_del_acuse_se_guarda_sin_espacios_alrededor` |
| O9 | `registrar_acuse` cuenta lo marcado, no lo pedido; un acuse de algo que nadie envió queda en la bitácora; la lista vacía no toca nada | `tests/test_orquestador.py::test_acuse_de_referencia_desconocida_no_inventa_entregas`, `tests/test_orquestador_bordes.py::test_acusar_una_lista_vacia_no_toca_nada`, `tests/test_persistencia_sqlite.py::test_un_acuse_sin_entrega_queda_en_la_bitacora_sin_llave_foranea_rota` |
| O10 | La bitácora es append-only y conserva la secuencia completa con su actor | `tests/test_orquestador.py::test_la_bitacora_conserva_la_secuencia_completa`, `tests/test_api_bordes.py::test_la_bitacora_de_una_entrega_y_el_limite_de_eventos`, `tests/test_orquestador_bordes.py::test_marcar_vencidas_deja_constancia_del_conciliador_como_actor` |

## 6. Reenvío idempotente

| # | Regla | Pruebas |
|---|---|---|
| I1 | Misma clave, misma entrega: reenviar en otra corrida no duplica filas ni envíos | `tests/test_orquestador.py::test_procesar_dos_veces_no_duplica`, `tests/test_reenvio_idempotente.py::test_reenviar_en_otra_corrida_lo_que_esta_vivo_no_lo_vuelve_a_enviar` |
| I2 | Un trabajo repetido en la MISMA corrida se procesa una vez, cualquiera que sea su desenlace (defecto corregido en la semana 3) | `tests/test_reenvio_idempotente.py::test_el_mismo_trabajo_dos_veces_en_una_corrida_se_procesa_una_vez` |
| I3 | El lote sembrado dos veces por los canales reales no duplica nada: resumen, brechas, buzones y lote idénticos | `tests/test_reenvio_idempotente.py::test_el_lote_sembrado_dos_veces_no_duplica_nada` |
| I4 | Reenviar lo acusado no lo reabre; acusar dos veces no cuenta dos ni cambia al autor ni la hora | `tests/test_reenvio_idempotente.py::test_reenviar_despues_del_acuse_no_lo_reabre`, `tests/test_reenvio_idempotente.py::test_acusar_dos_veces_no_cuenta_dos_ni_cambia_al_autor`, `tests/test_api_bordes.py::test_consumir_dos_veces_no_inventa_acuses` |
| I5 | La clave es trabajo + referencia; el acuse del receptor es por referencia | `tests/test_reenvio_idempotente.py::test_la_clave_es_trabajo_mas_referencia_y_el_acuse_es_por_referencia` |
| I6 | La base impide dos entregas con la misma clave aunque la aplicación se equivoque | `tests/test_persistencia_sqlite.py::test_la_base_impide_dos_entregas_con_la_misma_clave` |
| I7 | Los folios nuevos no reusan los anteriores (ni en memoria ni tras reiniciar sobre archivo) | `tests/test_api_bordes.py::test_corrida_con_trabajos_sin_ruta_queda_con_problemas_y_no_reusa_folios`, `tests/test_persistencia_sqlite.py::test_con_archivo_la_api_conserva_el_historico_y_no_reusa_folios` |

## 7. Conciliador: brecha de acuse · `acuse/conciliador.py`

| # | Regla | Pruebas |
|---|---|---|
| K1 | La brecha se mide por canal y no se promedia; crece mientras nadie consuma | `tests/test_conciliador.py::test_la_brecha_separa_los_canales`, `tests/test_conciliador.py::test_sin_consumir_la_brecha_crece` |
| K2 | Marcar vencidas es idempotente y no altera la brecha: mirar el problema no lo resuelve | `tests/test_conciliador.py::test_marcar_vencidas_es_idempotente_y_no_borra_la_brecha`, `tests/test_api.py::test_vencer_nombra_el_incumplimiento_pero_no_borra_la_brecha` |
| K3 | La antigüedad se mide desde el envío más viejo sin acuse, incluido lo ya marcado SIN_ACUSE; todo acusado es brecha cero | `tests/test_orquestador_bordes.py::test_la_antiguedad_de_la_brecha_cuenta_tambien_lo_ya_marcado_sin_acuse`, `tests/test_orquestador_bordes.py::test_un_canal_con_todo_acusado_no_tiene_brecha` |
| K4 | Sólo el acuse del receptor cierra la brecha, y exactamente por lo leído | `tests/test_conciliador.py::test_el_acuse_si_cierra_la_brecha`, `tests/test_api.py::test_consumir_reduce_la_brecha_y_solo_por_lo_leido` |
| K5 | Vencer pasa a SIN_ACUSE con el plazo y el exceso escritos | `tests/test_conciliador.py::test_marcar_vencidas_mueve_a_sin_acuse` |
| K6 | El resumen publica los DOS números (según el emisor y confirmado) y todos los estados, aun en cero | `tests/test_conciliador.py::test_resumen_distingue_segun_emisor_de_confirmado`, `tests/test_conciliador.py::test_el_resumen_deja_de_mentir_cuando_hay_acuse`, `tests/test_orquestador_bordes.py::test_en_una_base_vacia_no_hay_brechas_y_el_resumen_lista_todos_los_estados_en_cero`, `tests/test_api.py::test_resumen_publica_los_dos_numeros_y_la_diferencia` |
| K7 | Vencer no mejora la cifra confirmada | `tests/test_conciliador.py::test_vencer_no_mejora_el_numero_confirmado` |
| K8 | Lo rechazado no se esconde: no está en ningún canal, sí en el resumen | `tests/test_conciliador.py::test_los_rechazados_no_se_esconden_en_ningun_canal` |

## 8. Cifras control por lote · `acuse/cifras.py` ([ADR 0005](adr/0005-cifras-control-y-oraculo-independiente.md))

Especificación (la implementan, por separado, `acuse/cifras.py` y el oráculo `tests/golden/generar_golden.py`):
importe a centavos con mitad hacia arriba (el empate se aleja del cero; el cero no lleva signo); fila =
SHA-256 del JSON canónico `[referencia, "importe a 2 decimales", moneda]` (UTF-8, sin espacios, sin escapar
no-ASCII); lote = SHA-256 de los hashes de fila ordenados por referencia y unidos con `\n`; suma = suma de los
importes ya redondeados.

| # | Regla | Pruebas |
|---|---|---|
| M1 | El importe es Decimal redondeado a centavos mitad hacia arriba; el cero no lleva signo | `tests/test_bordes_importes.py::test_el_importe_se_redondea_a_centavos_mitad_hacia_arriba` |
| M2 | Lo que no es una cantidad exacta se rechaza: float, booleano, separadores de miles, notación científica en texto, no finito, fuera de rango | `tests/test_bordes_importes.py::test_lo_que_no_es_una_cantidad_exacta_se_rechaza` |
| M3 | La suma del lote es la suma de las filas ya redondeadas; conteo y suma salen juntos | `tests/test_bordes_importes.py::test_la_suma_del_lote_es_la_suma_de_las_filas_redondeadas`, `tests/test_cifras_control.py::test_conteo_suma_y_hash_del_lote` |
| M4 | Hash por fila sobre JSON canónico: igual al centavo da el mismo hash, cualquier campo lo cambia y un separador en la referencia no provoca colisión | `tests/test_bordes_importes.py::test_importes_iguales_al_centavo_dan_el_mismo_hash`, `tests/test_bordes_importes.py::test_cambiar_cualquier_campo_cambia_el_hash_de_la_fila`, `tests/test_bordes_importes.py::test_el_hash_de_fila_no_se_confunde_con_separadores_en_la_referencia` |
| M5 | La llave estable es la referencia: ni el orden de llegada ni el UUID de la entrega cambian las cifras | `tests/test_cifras_control.py::test_el_orden_de_llegada_no_cambia_las_cifras`, `tests/test_cifras_control.py::test_las_cifras_no_dependen_del_id_de_la_entrega`, `tests/test_paridad_golden.py::test_reordenar_las_filas_no_rompe_la_paridad` |
| M6 | Lo que obligaría a inventar se rechaza: referencia repetida o vacía, monedas mezcladas, importe ausente, moneda no ISO, lote sin nombre | `tests/test_cifras_control.py::test_lo_que_obligaria_a_inventar_se_rechaza`, `tests/test_cifras_control.py::test_un_lote_sin_nombre_se_rechaza` |
| M7 | La conciliación clasifica cada diferencia (FALTANTE, SOBRANTE, IMPORTE_DISTINTO, MONEDA_DISTINTA), ordenadas por referencia; lo idéntico CUADRA | `tests/test_cifras_control.py::test_lo_mismo_de_los_dos_lados_cuadra`, `tests/test_cifras_control.py::test_cada_alteracion_descuadra_con_la_diferencia_clasificada`, `tests/test_cifras_control.py::test_las_diferencias_salen_ordenadas_por_referencia`, `tests/test_cifras_control.py::test_el_receptor_sin_nada_de_un_lote_declarado_descuadra_todo` |
| M8 | Un lote sin filas no cuadra: es SIN_REGISTROS | `tests/test_cifras_control.py::test_un_lote_sin_filas_no_cuadra_es_sin_registros` |
| M9 | No se concilian lotes distintos y el emisor declara sólo los trabajos de su lote | `tests/test_cifras_control.py::test_no_se_concilian_lotes_distintos`, `tests/test_cifras_control.py::test_el_emisor_declara_solo_los_trabajos_de_ese_lote` |

## 9. Paridad contra el oráculo independiente · `tests/golden/`

| # | Regla | Pruebas |
|---|---|---|
| G1 | Emisor y receptor reproducen el golden del oráculo por el camino real (Orquestador → Enrutador → CanalLote), fila por fila | `tests/test_paridad_golden.py::test_el_conciliador_reproduce_el_golden`, `tests/test_paridad_golden.py::test_el_codigo_y_el_oraculo_coinciden_fila_por_fila` |
| G2 | El golden está al día con su oráculo, y la entrada del lote sembrado con la demo | `tests/test_paridad_golden.py::test_el_golden_esta_al_dia_con_su_oraculo`, `tests/test_paridad_golden.py::test_la_entrada_del_lote_sembrado_es_la_de_la_demo` |
| G3 | Control negativo: una alteración mínima (un centavo, una fila de más o de menos, otra moneda) rompe la paridad | `tests/test_paridad_golden.py::test_una_alteracion_minima_rompe_la_paridad` |
| G4 | La API publica las cifras del lote sembrado igual que el golden | `tests/test_paridad_golden.py::test_la_api_publica_las_cifras_del_lote_sembrado_igual_que_el_golden`, `tests/e2e/test_recorrido_e2e.py::test_recorrido_de_la_demo_y_reinicio_del_proceso` |

## 10. Persistencia (SQLite real) · `acuse/persistencia.py`

| # | Regla | Pruebas |
|---|---|---|
| P1 | Lo escrito sobrevive a cerrar y reabrir el archivo, y al reinicio del proceso | `tests/test_persistencia_sqlite.py::test_lo_escrito_sobrevive_a_cerrar_y_reabrir_el_archivo`, `tests/e2e/test_recorrido_e2e.py::test_recorrido_de_la_demo_y_reinicio_del_proceso` |
| P2 | El instante vuelve en UTC y es el mismo (también una hora repetida de otoño); lo ingenuo se guarda como UTC | `tests/test_persistencia_sqlite.py::test_el_archivo_devuelve_el_mismo_instante_en_utc`, `tests/test_persistencia_sqlite.py::test_una_fecha_sin_zona_se_guarda_como_utc` |
| P3 | La base no acepta un estado que el modelo no conoce, y uno escrito a mano no se lee en silencio | `tests/test_persistencia_sqlite.py::test_la_base_no_acepta_un_estado_que_el_modelo_no_conoce`, `tests/test_persistencia_sqlite.py::test_un_estado_desconocido_en_el_archivo_no_se_lee_en_silencio` |
| P4 | Crear el esquema es idempotente sobre un archivo con datos | `tests/test_persistencia_sqlite.py::test_crear_tablas_es_idempotente_sobre_un_archivo_con_datos` |
| P5 | Presupuesto de consultas: el tablero cuesta una consulta a cualquier tamaño; procesar, vencer y acusar, costo lineal | `tests/test_presupuesto_consultas.py::test_procesar_cuesta_lo_mismo_por_trabajo`, `tests/test_presupuesto_consultas.py::test_el_tablero_cuesta_una_consulta_sin_importar_el_tamano`, `tests/test_presupuesto_consultas.py::test_marcar_vencidas_lee_una_vez_y_escribe_por_vencida`, `tests/test_presupuesto_consultas.py::test_registrar_acuse_busca_una_vez_por_referencia` |

## 11. API y contrato · `acuse/api/app.py`, `docs/api/openapi.json`

| # | Regla | Pruebas |
|---|---|---|
| A1 | `/health` dice que el proceso responde, no que todo esté bien | `tests/test_api.py::test_health_responde_pero_no_afirma_que_todo_este_bien` |
| A2 | El contrato versionado es el que publica la app, cubre todas las rutas públicas y documenta cada operación con esquemas | `tests/test_contrato_openapi.py::test_el_contrato_versionado_es_el_que_publica_la_app`, `tests/test_contrato_openapi.py::test_el_contrato_cubre_todas_las_rutas_publicas`, `tests/test_contrato_openapi.py::test_cada_operacion_esta_documentada_con_esquemas` |
| A3 | La app cumple su contrato con datos generados desde él (Schemathesis, en proceso) | `tests/test_contrato_schemathesis.py::test_la_api_cumple_su_contrato` |
| A4 | Lo que el contrato no admite es 422 y no se coacciona; 6.0 sí es entero; un cuerpo ilegible es 400 | `tests/test_api_bordes.py::test_lo_que_el_contrato_no_admite_se_rechaza_con_422`, `tests/test_api_bordes.py::test_un_entero_escrito_como_6_punto_0_es_un_entero`, `tests/test_api_bordes.py::test_un_cuerpo_ilegible_es_400`, `tests/test_api_bordes.py::test_filtros_fuera_de_contrato_son_422` |
| A5 | Errores con motivo: canal o lote inexistente es 404 con lo que sí hay; consumir un síncrono es 409 | `tests/test_api_bordes.py::test_consumir_un_canal_que_no_existe_es_404_con_los_registrados`, `tests/test_api_bordes.py::test_las_cifras_de_un_lote_que_no_existe_son_404`, `tests/test_api_bordes.py::test_consumir_un_canal_sincrono_es_409_porque_no_tiene_buzon` |
| A6 | El tablero en «/» no tapa la API: método equivocado es 405, ruta inexistente 404; los sinónimos del tablero responden fuera del contrato | `tests/test_api_bordes.py::test_un_metodo_equivocado_es_405_no_404`, `tests/test_api_bordes.py::test_una_ruta_de_api_inexistente_es_404_y_el_tablero_sigue_en_la_raiz`, `tests/test_contrato_openapi.py::test_los_sinonimos_del_tablero_responden_fuera_del_contrato` |
| A7 | Los importes viajan como texto, nunca como número de punto flotante | `tests/test_contrato_openapi.py::test_los_importes_del_contrato_viajan_como_texto_no_como_numero` |
| A8 | El tablero lee lo mismo que el núcleo: consumir el lote acusa el corte, filtros por estado y canal, brechas igual al resumen, avance corto no vence nada | `tests/test_api_bordes.py::test_consumir_el_canal_por_lote_acusa_el_corte_completo`, `tests/test_api_bordes.py::test_los_filtros_de_entregas`, `tests/test_api_bordes.py::test_brechas_como_recurso_propio_coincide_con_el_resumen`, `tests/test_api_bordes.py::test_vencer_con_avance_explicito_corto_no_marca_nada` |
| A9 | El recorrido de la demo funciona contra la API arrancada como proceso | `tests/e2e/test_recorrido_e2e.py::test_recorrido_de_la_demo_y_reinicio_del_proceso` |

## 12. Arquitectura · `docs/arquitectura/README.md`

| # | Regla | Pruebas |
|---|---|---|
| X1 | Todo módulo del paquete tiene capa asignada | `tests/test_arquitectura.py::test_todo_modulo_del_paquete_tiene_capa` |
| X2 | Cada módulo importa sólo lo que su capa permite (el dominio, sólo biblioteca estándar) | `tests/test_arquitectura.py::test_cada_modulo_respeta_su_capa` |
| X3 | Control negativo: el verificador detecta cada clase de violación y deja pasar lo permitido | `tests/test_arquitectura.py::test_el_verificador_detecta_cada_violacion`, `tests/test_arquitectura.py::test_el_verificador_deja_pasar_lo_permitido` |
