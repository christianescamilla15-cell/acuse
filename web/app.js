/* ═══════════════════════════════════════════════════════════════════════════
   Acuse · tablero (JS vanilla, sin build ni dependencias)

   Consume la API del MISMO ORIGEN bajo /api/…

   Dos criterios de diseño, heredados de la tesis del proyecto:

   1) NADA SE SILENCIA. Cada fetch va en try/catch y todo fallo se pinta en la
      banda de errores con el método, la ruta, el código y el cuerpo. Un tablero
      que se queda con datos viejos porque la API murió es la misma patología
      que corrige Acuse: éxito aparente sin nada detrás.

   2) LOS DOS NÚMEROS SE CALCULAN AQUÍ, de la misma lista que alimenta la tabla.
      Así el contraste del panel principal y las filas de abajo no pueden
      contradecirse: son la misma verdad contada dos veces.
   ═══════════════════════════════════════════════════════════════════════════ */
'use strict';

/* ───────────────────────── Rutas de la API ─────────────────────────
   Se declaran varias candidatas por recurso y se memoriza la primera que
   responde: el tablero no debe romperse por un sinónimo en el nombre de la
   ruta. La ruta ganadora queda fija para las siguientes vueltas. */

const CANDIDATAS = {
  agregado: ['/api/estado', '/api/tablero'],           // opcional: todo en una llamada
  entregas: ['/api/entregas'],
  brechas:  ['/api/brechas', '/api/brecha'],
  corridas: ['/api/corridas'],
  resumen:  ['/api/resumen'],                          // sólo como red de seguridad
};

const ACCIONES = {
  lote:       ['/api/simular/lote', '/api/simular/correr-lote'],
  consumir:   ['/api/simular/consumir', '/api/simular/consumo', '/api/simular/acusar'],
  sin_insumo: ['/api/simular/sin-insumo', '/api/simular/sin_insumo', '/api/simular/corrida-vacia'],
  vencer:     ['/api/simular/vencer', '/api/simular/vencer-plazos', '/api/simular/vencer_plazos'],
  reiniciar:  ['/api/simular/reiniciar', '/api/simular/reset'],
};

const ETIQUETA_ACCION = {
  lote: 'Correr lote',
  consumir: 'Que el receptor consuma',
  sin_insumo: 'Corrida sin insumo',
  vencer: 'Vencer plazos',
  reiniciar: 'Reiniciar',
};

const ESTADOS = ['PENDIENTE', 'ENVIADO', 'ACUSADO', 'SIN_ACUSE', 'FALLIDO', 'RECHAZADO'];

/* ───────────────────────── Estado en memoria ───────────────────────── */

const app = {
  entregas: [],
  brechas: [],
  corridas: [],
  resumen: null,        // sólo se usa si la lista de entregas no se pudo traer
  rutaBuena: {},        // recurso → ruta que sí respondió
  ocupado: false,
  errores: [],
};

/* ───────────────────────── Utilidades ───────────────────────── */

const $ = (sel) => document.querySelector(sel);

/** Escapa texto antes de meterlo al DOM como HTML. */
function esc(valor) {
  if (valor === null || valor === undefined) return '';
  return String(valor)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/** Primer valor definido y no nulo entre varias claves posibles del objeto. */
function v(obj, ...claves) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (const k of claves) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

/** Normaliza a arreglo: acepta lista pelona o envuelta en un objeto. */
function comoLista(datos, ...claves) {
  if (Array.isArray(datos)) return datos;
  if (datos && typeof datos === 'object') {
    for (const k of [...claves, 'items', 'datos', 'resultados', 'data']) {
      if (Array.isArray(datos[k])) return datos[k];
    }
  }
  return [];
}

function entero(valor, porOmision = 0) {
  const n = Number(valor);
  return Number.isFinite(n) ? Math.trunc(n) : porOmision;
}

/** Marca de tiempo legible; '—' cuando el dato no existe (es información, no un hueco). */
function hora(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleTimeString('es-MX', { hour12: false });
}

/** Segundos → "45s" · "12m 30s" · "3h 04m" · "2d 05h". */
function duracion(segundos) {
  const s = Number(segundos);
  if (!Number.isFinite(s) || s < 0) return '—';
  const t = Math.trunc(s);
  if (t < 60) return `${t}s`;
  if (t < 3600) return `${Math.trunc(t / 60)}m ${String(t % 60).padStart(2, '0')}s`;
  if (t < 86400) return `${Math.trunc(t / 3600)}h ${String(Math.trunc((t % 3600) / 60)).padStart(2, '0')}m`;
  return `${Math.trunc(t / 86400)}d ${String(Math.trunc((t % 86400) / 3600)).padStart(2, '0')}h`;
}

/** Animación sutil al repintar un contenedor. */
function latir(nodo) {
  if (!nodo) return;
  nodo.classList.remove('refrescado');
  void nodo.offsetWidth;               // fuerza reflow para poder repetir la animación
  nodo.classList.add('refrescado');
  nodo.addEventListener('animationend', () => nodo.classList.remove('refrescado'), { once: true });
}

/* ───────────────────────── Capa de red ───────────────────────── */

class ErrorApi extends Error {
  constructor(mensaje, ruta, codigo) {
    super(mensaje);
    this.ruta = ruta;
    this.codigo = codigo;
  }
}

/**
 * fetch + JSON con el error SIEMPRE legible.
 * Distingue tres fallos que suelen confundirse: no hubo red, hubo respuesta con
 * código de error, o hubo 200 con un cuerpo que no es JSON.
 */
async function pedirJSON(ruta, opciones = {}) {
  let respuesta;
  try {
    respuesta = await fetch(ruta, {
      headers: { 'Accept': 'application/json' },
      cache: 'no-store',
      ...opciones,
    });
  } catch (fallo) {
    throw new ErrorApi(
      `no hubo respuesta de ${ruta} — ¿el servidor de la API está corriendo en este mismo origen? (${fallo && fallo.message ? fallo.message : 'fetch falló'})`,
      ruta, 0,
    );
  }

  const crudo = await respuesta.text().catch(() => '');

  if (!respuesta.ok) {
    const extracto = crudo ? ` · cuerpo: ${crudo.slice(0, 240)}` : '';
    throw new ErrorApi(`${respuesta.status} ${respuesta.statusText} en ${ruta}${extracto}`, ruta, respuesta.status);
  }

  if (!crudo.trim()) return null;      // 204 o cuerpo vacío: válido, sin datos

  try {
    return JSON.parse(crudo);
  } catch (_) {
    throw new ErrorApi(`${ruta} respondió 200 pero el cuerpo no es JSON: ${crudo.slice(0, 240)}`, ruta, respuesta.status);
  }
}

/**
 * Prueba las rutas candidatas de un recurso hasta que una responda y memoriza
 * la ganadora. Si ninguna sirve, lanza el error de la PRIMERA (la esperada),
 * que es la que le dice algo útil a quien lea la banda de errores.
 */
async function pedirRecurso(clave, opciones = {}) {
  const memorizada = app.rutaBuena[clave];
  const candidatas = memorizada ? [memorizada] : (CANDIDATAS[clave] || []);
  let primerFallo = null;

  for (const ruta of candidatas) {
    try {
      const datos = await pedirJSON(ruta, opciones);
      app.rutaBuena[clave] = ruta;
      return datos;
    } catch (fallo) {
      if (!primerFallo) primerFallo = fallo;
      // 404/405 = ese sinónimo no existe: se prueba el siguiente.
      // Cualquier otro código sí es un fallo real y se corta aquí.
      if (!(fallo instanceof ErrorApi) || (fallo.codigo !== 404 && fallo.codigo !== 405)) throw fallo;
      if (memorizada) delete app.rutaBuena[clave];
    }
  }
  throw primerFallo || new ErrorApi(`no hay ruta declarada para «${clave}»`, '(ninguna)', 0);
}

/* ───────────────────────── Normalizadores ─────────────────────────
   El tablero no asume el nombre exacto de cada campo; acepta las variantes
   razonables y deja constancia cuando no encuentra nada. */

function normEntrega(e) {
  return {
    id: v(e, 'id', 'entrega_id', 'entregaId') ?? '',
    referencia: v(e, 'referencia', 'ref', 'reference') ?? '(sin referencia)',
    canal: v(e, 'canal', 'channel') ?? '(sin canal)',
    estado: String(v(e, 'estado', 'status') ?? 'PENDIENTE').toUpperCase(),
    intentos: entero(v(e, 'intentos', 'attempts'), 0),
    enviado_en: v(e, 'enviado_en', 'enviadoEn', 'sent_at'),
    acusado_en: v(e, 'acusado_en', 'acusadoEn', 'acked_at'),
    acuse_por: v(e, 'acuse_por', 'acusePor', 'acked_by'),
    detalle: v(e, 'detalle', 'motivo', 'detail', 'razon') ?? '',
  };
}

function normBrecha(b) {
  const enviados = entero(v(b, 'enviados', 'enviado', 'sent'), 0);
  const acusados = entero(v(b, 'acusados', 'acusado', 'acked'), 0);
  const sinAcuse = entero(v(b, 'sin_acuse', 'sinAcuse', 'sin_acusar'), 0);
  return {
    canal: v(b, 'canal', 'channel') ?? '(sin canal)',
    enviados, acusados, sin_acuse: sinAcuse,
    mas_antiguo_s: entero(v(b, 'mas_antiguo_s', 'masAntiguoS', 'antiguedad_s', 'mas_antiguo'), 0),
    hay_brecha: v(b, 'hay_brecha', 'hayBrecha') ?? (sinAcuse > 0),
  };
}

function normCorrida(c) {
  return {
    id: v(c, 'id', 'corrida_id', 'lote', 'run_id') ?? '(sin id)',
    lote: v(c, 'lote', 'batch'),
    estado: String(v(c, 'estado', 'status', 'resultado') ?? 'DESCONOCIDO').toUpperCase(),
    inicio: v(c, 'iniciada_en', 'inicio', 'comenzo_en', 'started_at', 'creado_en'),
    fin: v(c, 'terminada_en', 'fin', 'termino_en', 'finished_at'),
    leidos: v(c, 'leidos', 'insumos', 'entradas', 'trabajos_leidos'),
    procesados: v(c, 'procesados', 'trabajos', 'total', 'entregas_creadas', 'generadas'),
    detalle: v(c, 'detalle', 'motivo', 'nota', 'detail') ?? '',
    crudo: c,
  };
}

/** Clase CSS del estado de una entrega. */
function claseEstado(estado) {
  return ESTADOS.includes(estado) ? `e-${estado}` : 'e-OTRO';
}

/** Clasificación del estado de una corrida (tolerante al vocabulario de la API). */
function claseCorrida(estado) {
  const t = String(estado || '').toUpperCase();
  if (t.includes('SIN_INSUMO') || t.includes('SIN INSUMO') || t.includes('VACIA') || t.includes('VACÍA')) return 'SIN_INSUMO';
  if (t.includes('FALL') || t.includes('ERROR') || t.includes('ABORT')) return 'FALLA';
  if (t.includes('EXITO') || t.includes('ÉXITO') || t.includes('OK') || t.includes('COMPLET') || t.includes('TERMIN')) return 'OK';
  return 'OTRO';
}

/* ───────────────────────── Errores visibles ───────────────────────── */

function limpiarErrores() { app.errores = []; }

function anotarError(contexto, fallo) {
  const msg = fallo && fallo.message ? fallo.message : String(fallo);
  app.errores.push(`${contexto}: ${msg}`);
}

function pintarErrores() {
  const caja = $('#errores');
  if (!caja) return;
  if (!app.errores.length) {
    caja.hidden = true;
    caja.innerHTML = '';
    return;
  }
  caja.hidden = false;
  caja.innerHTML = app.errores
    .map((e) => `<p class="errores__linea"><span class="errores__marca">FALLO</span> ${esc(e)}</p>`)
    .join('');
}

/* ───────────────────────── Carga de datos ───────────────────────── */

async function cargarTodo() {
  limpiarErrores();

  // Intento 1: un solo agregado, si la API lo ofrece.
  let agregado = null;
  try {
    agregado = await pedirRecurso('agregado');
  } catch (_) {
    agregado = null;               // no existe: se piden los recursos por separado, sin ruido
  }

  const tomaDelAgregado = agregado && typeof agregado === 'object';

  // Entregas — fuente de verdad de la tabla Y de los dos números grandes.
  if (tomaDelAgregado && (Array.isArray(agregado.entregas) || Array.isArray(agregado.deliveries))) {
    app.entregas = comoLista(agregado.entregas || agregado.deliveries).map(normEntrega);
  } else {
    try {
      app.entregas = comoLista(await pedirRecurso('entregas'), 'entregas').map(normEntrega);
    } catch (fallo) {
      anotarError('al traer las entregas', fallo);
      app.entregas = [];
      // Red de seguridad: si hay un resumen del servidor, al menos el contraste se pinta.
      try {
        app.resumen = await pedirRecurso('resumen');
      } catch (_) {
        app.resumen = null;
      }
    }
  }

  // Brechas por canal — si la API no las expone, se calculan aquí.
  if (tomaDelAgregado && Array.isArray(agregado.brechas)) {
    app.brechas = agregado.brechas.map(normBrecha);
  } else {
    try {
      app.brechas = comoLista(await pedirRecurso('brechas'), 'brechas').map(normBrecha);
    } catch (fallo) {
      anotarError('al traer la brecha por canal', fallo);
      app.brechas = calcularBrechas(app.entregas);
    }
  }
  if (!app.brechas.length && app.entregas.length) app.brechas = calcularBrechas(app.entregas);

  // Corridas.
  if (tomaDelAgregado && Array.isArray(agregado.corridas)) {
    app.corridas = agregado.corridas.map(normCorrida);
  } else {
    try {
      app.corridas = comoLista(await pedirRecurso('corridas'), 'corridas').map(normCorrida);
    } catch (fallo) {
      anotarError('al traer las corridas', fallo);
      app.corridas = [];
    }
  }

  pintarTodo();
}

/**
 * Brecha por canal calculada del lado del tablero.
 * Sólo se usa cuando la API no la expone; el criterio es el mismo del modelo:
 * enviados = ENVIADO · acusados = ACUSADO · sin acuse = SIN_ACUSE.
 */
function calcularBrechas(entregas) {
  const porCanal = new Map();
  for (const e of entregas) {
    const canal = e.canal || '(sin canal)';
    if (!porCanal.has(canal)) {
      porCanal.set(canal, { canal, enviados: 0, acusados: 0, sin_acuse: 0, mas_antiguo_s: 0 });
    }
    const b = porCanal.get(canal);
    if (e.estado === 'ENVIADO') {
      b.enviados += 1;
      if (e.enviado_en) {
        const edad = Math.trunc((Date.now() - new Date(e.enviado_en).getTime()) / 1000);
        if (Number.isFinite(edad) && edad > b.mas_antiguo_s) b.mas_antiguo_s = edad;
      }
    }
    if (e.estado === 'ACUSADO') b.acusados += 1;
    if (e.estado === 'SIN_ACUSE') b.sin_acuse += 1;
  }
  return [...porCanal.values()]
    .map((b) => ({ ...b, hay_brecha: b.sin_acuse > 0 }))
    .sort((a, b) => a.canal.localeCompare(b.canal));
}

/* ───────────────────────── Pintado ───────────────────────── */

function pintarTodo() {
  pintarErrores();
  pintarContraste();
  pintarBrechas();
  pintarFiltroCanal();
  pintarTabla();
  pintarCorridas();
}

/** ⭐ El panel principal: los dos números que casi todos los sistemas confunden. */
function pintarContraste() {
  const conteo = Object.fromEntries(ESTADOS.map((s) => [s, 0]));
  for (const e of app.entregas) {
    if (conteo[e.estado] === undefined) conteo[e.estado] = 0;
    conteo[e.estado] += 1;
  }

  let segunEmisor = conteo.ENVIADO + conteo.ACUSADO;   // lo que el emisor da por hecho
  let confirmado = conteo.ACUSADO;                     // lo único que de verdad llegó

  // Si la lista de entregas no se pudo traer pero hay resumen del servidor, se usa.
  if (!app.entregas.length && app.resumen && typeof app.resumen === 'object') {
    const r = app.resumen;
    segunEmisor = entero(v(r, 'segun_emisor', 'segunEmisor', 'entregado_segun_emisor'), segunEmisor);
    confirmado = entero(v(r, 'confirmado', 'acusados', 'entregado_confirmado'), confirmado);
  }

  const diferencia = Math.max(0, segunEmisor - confirmado);

  $('#cifra-emisor').textContent = segunEmisor;
  $('#cifra-confirmado').textContent = confirmado;

  const caja = $('#diferencia');
  if (diferencia > 0) {
    caja.dataset.brecha = 'si';
    caja.innerHTML =
      `<span class="num-brecha">${diferencia}</span> ` +
      (diferencia === 1
        ? 'entrega que el emisor da por hecha y nadie ha confirmado'
        : 'entregas que el emisor da por hechas y nadie ha confirmado');
  } else if (segunEmisor === 0) {
    caja.dataset.brecha = 'neutro';
    caja.textContent = 'Todavía no hay nada enviado. Corre un lote para ver el contraste.';
  } else {
    caja.dataset.brecha = 'no';
    caja.textContent = 'Cero entregas sin confirmar: todo lo que el emisor dio por hecho tiene acuse de alguien.';
  }

  // Marcadores del resto del universo (el contraste no cuenta toda la historia).
  const marcas = [
    ['TOTAL', app.entregas.length, 'var(--dim)'],
    ['PENDIENTE', conteo.PENDIENTE, 'var(--gris)'],
    ['SIN_ACUSE', conteo.SIN_ACUSE, 'var(--rojo)'],
    ['FALLIDO', conteo.FALLIDO, 'var(--rojo)'],
    ['RECHAZADO', conteo.RECHAZADO, 'var(--rojo)'],
  ];
  $('#marcadores').innerHTML = marcas
    .map(([t, n, color]) => `<li>${esc(t)} <b style="color:${color}">${entero(n)}</b></li>`)
    .join('');

  latir($('#panel-contraste'));
}

function pintarBrechas() {
  const caja = $('#brechas');
  if (!app.brechas.length) {
    caja.innerHTML = '<p class="vacio">Sin canales con actividad todavía.</p>';
    return;
  }

  caja.innerHTML = app.brechas.map((b) => {
    const conBrecha = b.sin_acuse > 0;
    const edad = b.mas_antiguo_s > 0
      ? `el más viejo sin confirmar lleva <strong>${esc(duracion(b.mas_antiguo_s))}</strong>`
      : 'nada esperando confirmación';
    return `
      <article class="tarjeta ${conBrecha ? 'tarjeta--brecha' : 'tarjeta--sana'}">
        <h3 class="tarjeta__canal">${esc(b.canal)}</h3>
        <div class="metricas">
          <div class="metrica metrica--enviados">
            <span class="metrica__n">${entero(b.enviados)}</span>
            <span class="metrica__t">enviados</span>
          </div>
          <div class="metrica metrica--acusados">
            <span class="metrica__n">${entero(b.acusados)}</span>
            <span class="metrica__t">acusados</span>
          </div>
          <div class="metrica metrica--sinacuse" data-cero="${b.sin_acuse > 0 ? 'no' : 'si'}">
            <span class="metrica__n">${entero(b.sin_acuse)}</span>
            <span class="metrica__t">sin acuse</span>
          </div>
        </div>
        <p class="tarjeta__pie">${edad}</p>
      </article>`;
  }).join('');

  latir(caja);
}

/** El selector de canal se arma con los canales que existen, conservando la selección. */
function pintarFiltroCanal() {
  const select = $('#filtro-canal');
  const previo = select.value || 'TODOS';
  const canales = [...new Set(app.entregas.map((e) => e.canal).filter(Boolean))].sort();
  select.innerHTML = ['TODOS', ...canales]
    .map((c) => `<option value="${esc(c)}">${esc(c)}</option>`)
    .join('');
  select.value = canales.includes(previo) || previo === 'TODOS' ? previo : 'TODOS';
}

function pintarTabla() {
  const cuerpo = $('#cuerpo-entregas');
  const fEstado = $('#filtro-estado').value;
  const fCanal = $('#filtro-canal').value;

  const filas = app.entregas.filter((e) =>
    (fEstado === 'TODOS' || e.estado === fEstado) &&
    (fCanal === 'TODOS' || e.canal === fCanal));

  $('#conteo-tabla').textContent = `${filas.length} de ${app.entregas.length} entregas`;

  if (!filas.length) {
    cuerpo.innerHTML = `<tr><td colspan="8" class="vacio">${
      app.entregas.length ? 'Ninguna entrega casa con el filtro.' : 'Aún no hay entregas. Corre un lote.'
    }</td></tr>`;
    return;
  }

  cuerpo.innerHTML = filas.map((e) => {
    // El motivo de un RECHAZADO se muestra COMPLETO: es el ejemplo vivo del
    // ruteo explícito — el trabajo no cayó a un canal por omisión, se rechazó
    // con el porqué escrito. Recortarlo aquí sería repetir el fallo original.
    const esRechazo = e.estado === 'RECHAZADO';
    const detalle = e.detalle
      ? `<span class="${esRechazo ? 'detalle--completo' : 'detalle'}">${esc(e.detalle)}</span>`
      : '<span class="tenue">—</span>';

    const enviado = hora(e.enviado_en);
    const acusado = hora(e.acusado_en);

    return `
      <tr>
        <td class="ref" title="${esc(e.id)}">${esc(e.referencia)}</td>
        <td>${esc(e.canal)}</td>
        <td><span class="insignia ${claseEstado(e.estado)}">${esc(e.estado)}</span></td>
        <td class="num">${entero(e.intentos)}</td>
        <td class="${enviado ? '' : 'tenue'}" title="${esc(e.enviado_en || '')}">${esc(enviado || '—')}</td>
        <td class="${acusado ? '' : 'tenue'}" title="${esc(e.acusado_en || '')}">${esc(acusado || '—')}</td>
        <td class="${e.acuse_por ? '' : 'tenue'}">${esc(e.acuse_por || '—')}</td>
        <td class="detalle">${detalle}</td>
      </tr>`;
  }).join('');

  latir(cuerpo);
}

function pintarCorridas() {
  const caja = $('#corridas');
  if (!app.corridas.length) {
    caja.innerHTML = '<p class="vacio">Todavía no se ha corrido nada.</p>';
    return;
  }

  caja.innerHTML = app.corridas.map((c) => {
    const clase = claseCorrida(c.estado);
    const sinInsumo = clase === 'SIN_INSUMO';

    const datos = [];
    if (c.lote !== undefined && c.lote !== null && c.lote !== c.id) datos.push(['lote', c.lote]);
    if (c.leidos !== undefined) datos.push(['leídos', c.leidos]);
    if (c.procesados !== undefined) datos.push(['procesados', c.procesados]);
    const ini = hora(c.inicio);
    const fin = hora(c.fin);
    if (ini) datos.push(['inicio', ini]);
    if (fin) datos.push(['fin', fin]);

    const filasDatos = datos
      .map(([t, val]) => `<span>${esc(t)}: <b>${esc(val)}</b></span>`)
      .join('');

    // Ámbar y con la leyenda que la desenmascara: terminar sin error no es haber hecho algo.
    const leyenda = sinInsumo
      ? '<p class="leyenda-sin-insumo">corrió y no procesó nada — esto NO es éxito</p>'
      : (c.detalle ? `<p class="tarjeta__pie">${esc(c.detalle)}</p>` : '');

    return `
      <article class="tarjeta ${sinInsumo ? 'tarjeta--sin-insumo' : ''}">
        <div class="corrida__cab">
          <span class="corrida__id">${esc(c.id)}</span>
          <span class="insignia c-${clase}">${esc(c.estado)}</span>
        </div>
        <div class="corrida__datos">${filasDatos || '<span class="tenue">sin métricas reportadas</span>'}</div>
        ${leyenda}
      </article>`;
  }).join('');

  latir(caja);
}

/* ───────────────────────── Acciones de simulación ───────────────────────── */

function bloquear(ocupado, texto) {
  app.ocupado = ocupado;
  const aviso = $('#estado-accion');
  aviso.textContent = texto;
  aviso.dataset.ocupado = ocupado ? 'si' : 'no';
  document.querySelectorAll('.boton').forEach((b) => { b.disabled = ocupado; });
}

async function simular(accion) {
  if (app.ocupado) return;
  const rutas = ACCIONES[accion] || [];
  bloquear(true, `${ETIQUETA_ACCION[accion] || accion}…`);

  let ultimoFallo = null;
  let listo = false;

  for (const ruta of rutas) {
    try {
      await pedirJSON(ruta, {
        method: 'POST',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: '{}',
      });
      listo = true;
      break;
    } catch (fallo) {
      ultimoFallo = fallo;
      // Sólo se prueba el siguiente sinónimo si la ruta no existía.
      if (!(fallo instanceof ErrorApi) || (fallo.codigo !== 404 && fallo.codigo !== 405)) break;
    }
  }

  bloquear(false, listo ? `${ETIQUETA_ACCION[accion] || accion}: hecho` : 'la acción falló');

  // Se refresca SIEMPRE, haya salido bien o mal: el tablero debe reflejar lo que
  // hay en el servidor, no lo que suponemos que dejó la acción.
  await cargarTodo();

  if (!listo && ultimoFallo) {
    anotarError(`al ejecutar «${ETIQUETA_ACCION[accion] || accion}»`, ultimoFallo);
    pintarErrores();
  }
}

/* ───────────────────────── Arranque ───────────────────────── */

function conectar() {
  document.querySelectorAll('[data-accion]').forEach((boton) => {
    boton.addEventListener('click', () => { simular(boton.dataset.accion); });
  });

  $('#boton-refrescar').addEventListener('click', async () => {
    if (app.ocupado) return;
    bloquear(true, 'refrescando…');
    await cargarTodo();
    bloquear(false, 'listo');
  });

  $('#filtro-estado').addEventListener('change', pintarTabla);
  $('#filtro-canal').addEventListener('change', pintarTabla);

  // Refresco periódico: la brecha crece con el tiempo aunque nadie toque nada.
  // Se salta si hay una acción en curso o si la pestaña no está a la vista.
  setInterval(() => {
    if (app.ocupado || document.hidden) return;
    cargarTodo().catch((fallo) => { anotarError('en el refresco automático', fallo); pintarErrores(); });
  }, 12000);
}

document.addEventListener('DOMContentLoaded', () => {
  conectar();
  cargarTodo().catch((fallo) => {
    // Última red: ni siquiera la carga inicial debe morir en silencio.
    anotarError('al cargar el tablero', fallo);
    pintarErrores();
  });
});
