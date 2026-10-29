/* MasterSeg Monitor — front sem dependências.
   Cada tela busca um JSON e redesenha; a atualização é por polling
   e pausa quando a aba não está visível. */
(() => {
  "use strict";

  const INTERVALO_MS = 10000;
  const $ = (sel, raiz = document) => raiz.querySelector(sel);

  // ------------------------------------------------------------ utilidades
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

  const fmtHora = (iso) => new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const fmtData = (iso) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  const hoje = (iso) => new Date(iso).toDateString() === new Date().toDateString();

  function ha(iso) {
    if (!iso) return "nunca";
    const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (s < 60) return "agora";
    const m = Math.floor(s / 60);
    if (m < 60) return `há ${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `há ${h} h${m % 60 ? ` ${m % 60} min` : ""}`;
    const d = Math.floor(h / 24);
    return `há ${d} dia${d > 1 ? "s" : ""}`;
  }

  const ROTULO_STATUS = { online: "Online", degradado: "Degradado", offline: "Offline", desconhecido: "Sem dados" };

  async function buscarJSON(url) {
    const resp = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (resp.status === 403 || resp.redirected) { location.reload(); throw new Error("sessão expirada"); }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  // Polling com indicador "ao vivo" no topo.
  function aoVivo(url, desenhar) {
    const ind = $("#ao-vivo"), txt = $("#ao-vivo-texto");
    let ultimo = null, timer = null;
    const ciclo = async () => {
      clearTimeout(timer);
      try {
        desenhar(await buscarJSON(url));
        ultimo = Date.now();
        ind.className = "ao-vivo ok";
        txt.textContent = "Ao vivo";
      } catch (e) {
        ind.className = "ao-vivo erro";
        txt.textContent = "Sem conexão com o servidor";
        console.error(e);
      }
      if (!document.hidden) timer = setTimeout(ciclo, INTERVALO_MS);
    };
    setInterval(() => {
      if (ultimo && ind.classList.contains("ok")) {
        const s = Math.round((Date.now() - ultimo) / 1000);
        txt.textContent = s < 3 ? "Ao vivo" : `Atualizado há ${s}s`;
      }
    }, 1000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) ciclo(); });
    ciclo();
    return ciclo; // para atualizar na hora depois de uma ação
  }

  // Sparkline de latência; lacunas (null) viram faixa vermelha de queda.
  function sparkline(valores, w, h) {
    if (!valores || !valores.length) {
      return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="0" y1="${h - 1}" x2="${w}" y2="${h - 1}" stroke="var(--linha)" stroke-dasharray="3 3"/></svg>`;
    }
    const n = valores.length, passo = n > 1 ? w / (n - 1) : w;
    const nums = valores.filter((v) => v != null);
    const max = Math.max(5, ...nums) * 1.15;
    const y = (v) => (h - 2) - (v / max) * (h - 6);
    let caminhos = [], atual = [], quedas = "";
    valores.forEach((v, i) => {
      const x = (i * passo).toFixed(1);
      if (v == null) {
        if (atual.length) caminhos.push(atual), (atual = []);
        quedas += `<rect class="queda-grafico" x="${Math.max(0, i * passo - passo / 2).toFixed(1)}" y="0" width="${Math.max(passo, 2).toFixed(1)}" height="${h}"/>`;
      } else atual.push([x, y(v).toFixed(1)]);
    });
    if (atual.length) caminhos.push(atual);
    const linhas = caminhos.map((pts) => {
      if (pts.length === 1) pts.push([(+pts[0][0] + 1).toFixed(1), pts[0][1]]);
      const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join("");
      const area = `${d}L${pts[pts.length - 1][0]},${h}L${pts[0][0]},${h}Z`;
      return `<path class="area-grafico" d="${area}"/><path class="linha-grafico" d="${d}"/>`;
    }).join("");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${quedas}${linhas}</svg>`;
  }

  // ------------------------------------------------------------ eventos
  function itemEvento(ev, { mostrarCliente = true } = {}) {
    const st = !ev.problema ? "online" : ev.severidade === "critico" ? "offline" : ev.severidade === "aviso" ? "degradado" : "desconhecido";
    const ultima = ev.ultima_ocorrencia || ev.criado_em;
    const quando = hoje(ultima) ? fmtHora(ultima) : fmtData(ultima);
    const sub = [
      mostrarCliente ? `<a href="/cliente/${encodeURIComponent(ev.cliente.chave)}/">${esc(ev.cliente.chave)} ${esc(ev.cliente.nome)}</a>` : "",
      `<span>${ha(ultima)}</span>`,
      ev.ocorrencias > 1 ? `<span class="ev-vezes">${ev.ocorrencias}× desde ${hoje(ev.criado_em) ? fmtHora(ev.criado_em) : fmtData(ev.criado_em)}</span>` : "",
      ev.problema && ev.resolvido ? "<span>Resolvido</span>" : "",
    ].join("");
    const decisao = ev.problema ? `<span class="decisao" data-d="${esc(ev.decisao)}">${esc(ev.decisao_label)}</span>` : "";
    const analise = ev.problema ? `
      <div class="ev-analise">
        ${ev.detalhe ? `<p>${esc(ev.detalhe)}</p>` : ""}
        <p><b>${esc(ev.decisao_label)}${ev.prioridade ? `, prioridade ${ev.prioridade}` : ""}.</b>
        ${esc(ev.justificativa || "Aguardando análise.")}
        ${ev.analisado_por ? ` <span>(${ev.analisado_por === "ia" ? "análise da IA" : ev.aguarda_ia ? "regra automática; a IA ainda vai revisar" : "regra automática"})</span>` : ""}</p>
      </div>` : "";
    return `<li class="ev${ev.resolvido ? " resolvido" : ""}" data-id="${ev.id}">
      <button class="ev-linha" type="button" aria-expanded="false" ${ev.problema ? "" : "disabled"}>
        <span class="ev-hora">${quando}</span><i class="st st-${st}"></i>
        <span><span class="ev-titulo">${esc(ev.titulo)}</span><span class="ev-sub">${sub}</span></span>
        ${decisao}
      </button>${analise}</li>`;
  }

  function desenharFeed(el, eventos, opcoes) {
    const abertos = new Set([...el.querySelectorAll(".ev.aberto")].map((li) => li.dataset.id));
    el.innerHTML = eventos.length
      ? eventos.map((e) => itemEvento(e, opcoes)).join("")
      : `<li class="feed-vazio">Nenhum evento registrado ainda. Assim que o agente reportar, eles aparecem aqui.</li>`;
    abertos.forEach((id) => {
      const li = el.querySelector(`.ev[data-id="${id}"]`);
      if (li) li.classList.add("aberto"), li.querySelector(".ev-linha").setAttribute("aria-expanded", "true");
    });
  }

  document.addEventListener("click", (e) => {
    const botao = e.target.closest(".ev-linha");
    if (!botao || e.target.closest("a")) return;
    const li = botao.parentElement;
    const aberto = li.classList.toggle("aberto");
    botao.setAttribute("aria-expanded", String(aberto));
  });

  // ------------------------------------------------------------ busca
  function iniciarBusca() {
    const input = $("#busca"), lista = $("#busca-lista");
    if (!input) return;
    let itens = [], sel = -1, seq = 0, atraso;

    const fechar = () => { lista.hidden = true; input.setAttribute("aria-expanded", "false"); sel = -1; };
    const marcar = () => lista.querySelectorAll("a").forEach((a, i) => a.setAttribute("aria-selected", String(i === sel)));

    async function pesquisar() {
      const q = input.value.trim();
      if (!q) return fechar();
      const meu = ++seq;
      const dados = await buscarJSON(`/api/busca/?q=${encodeURIComponent(q)}`).catch(() => null);
      if (!dados || meu !== seq) return;
      itens = dados.resultados;
      sel = itens.length ? 0 : -1;
      lista.innerHTML = itens.length ? itens.map((c, i) => `
        <li role="option"><a href="/cliente/${encodeURIComponent(c.chave)}/" aria-selected="${i === 0}">
          <span class="chave">${esc(c.chave)}</span>
          <span>${esc(c.nome)} <span class="cid">${esc(c.cidade)}</span></span>
          <i class="st st-${c.offline ? "offline" : "online"}" title="${c.offline} offline de ${c.total}"></i>
        </a></li>`).join("")
        : `<li class="vazio">Nenhum cliente com “${esc(q)}”. Confira a chave ou cadastre em Cadastros.</li>`;
      lista.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    input.addEventListener("input", () => { clearTimeout(atraso); atraso = setTimeout(pesquisar, 140); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" && itens.length) { sel = (sel + 1) % itens.length; marcar(); e.preventDefault(); }
      else if (e.key === "ArrowUp" && itens.length) { sel = (sel - 1 + itens.length) % itens.length; marcar(); e.preventDefault(); }
      else if (e.key === "Enter" && sel >= 0) { location.href = `/cliente/${encodeURIComponent(itens[sel].chave)}/`; }
      else if (e.key === "Escape") { fechar(); input.blur(); }
    });
    input.addEventListener("blur", () => setTimeout(fechar, 150));
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && !/input|textarea/i.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
    });
  }

  function iniciarRelogio() {
    const el = $("#relogio");
    if (!el) return;
    const tic = () => { el.textContent = new Date().toLocaleTimeString("pt-BR"); };
    tic(); setInterval(tic, 1000);
  }

  // Mesmas frases na visão geral e na página de agentes: agente e equipamentos
  // são coisas diferentes e cada tela diz de qual está falando.
  const ROTULO_AGENTE = {
    comunicando: "Agente: comunicando", pausado: "Agente: pausado (status congelados)",
    sem_contato: "Agente: SEM CONTATO (status congelados)", sem_agente: "Agente: não instalado",
  };
  const resumoAgente = (estado) => ROTULO_AGENTE[estado] || "";
  const MEDALHA = `<svg class="medalha" viewBox="0 0 24 24" role="img" aria-label="Cliente GOLD"><title>Cliente GOLD</title><path d="M6 1h4.5L13 8.5 9.5 9.5z" fill="#3b6fd8"/><path d="M18 1h-4.5L11 8.5l3.5 1z" fill="#2a56b8"/><circle cx="12" cy="15.2" r="7" fill="#f2c230" stroke="#a87b06" stroke-width="1.2"/><path d="M12 11l1 2.6 2.8.2-2.2 1.8.8 2.7L12 16.7l-2.4 1.6.8-2.7-2.2-1.8 2.8-.2z" fill="#fff4c2" stroke="#a87b06" stroke-width=".5"/></svg>`;

  function resumoEquipamentos(eq) {
    const linhas = [`Equipamentos: ${eq.online} de ${eq.total} online`];
    if (eq.offline) linhas.push(`Offline: ${eq.nomes_offline.join(", ")}`);
    if (eq.degradado) linhas.push(`Degradado: ${eq.nomes_degradado.join(", ")}`);
    if (eq.desconhecido) linhas.push(`${eq.desconhecido} sem dados ainda`);
    return linhas;
  }

  // ------------------------------------------------------------ dashboard
  function dashboard() {
    const kpis = $("#kpis"), mosaico = $("#mosaico");
    mosaico.innerHTML = Array(12).fill('<div class="esqueleto"></div>').join("");

    aoVivo("/api/dashboard/", (d) => {
      const k = d.kpis;
      const comFalha = d.mosaico.filter((m) => m.status === "offline" || m.status === "degradado").length;
      $("#mosaico-resumo").innerHTML = comFalha
        ? `<strong>${comFalha} de ${k.clientes}</strong> clientes com algum problema agora`
        : `Todos os <strong>${k.clientes}</strong> clientes sem falhas`;

      mosaico.innerHTML = d.mosaico.map((m) => {
        const lf = (m.links_fora || []).length;
        const eq = m.equipamentos || {};
        const conta = m.agente_offline ? "agente sem contato"
          : eq.offline ? `${eq.offline} equip. offline`
          : lf ? "link fora"
          : eq.degradado ? `${eq.degradado} degradado${eq.degradado > 1 ? "s" : ""}`
          : `${eq.online} online`;
        const tec = m.chamar_tecnico || [];
        const titulo = [resumoAgente(m.agente), resumoEquipamentos(eq).join("\n"),
          lf ? `Link fora: ${m.links_fora.join(", ")}` : "",
          tec.length ? `\nPrecisa de técnico:\n${tec.map((t) => `• ${t}`).join("\n")}` : ""].filter(Boolean).join("\n");
        return `<a class="celula" data-st="${m.status}"${tec.length ? ' data-tecnico="true"' : ""} href="/cliente/${encodeURIComponent(m.chave)}/" title="${esc(titulo)}">
          <span class="c-chave">${esc(m.chave)}</span><span class="c-conta">${conta}</span>
          <span class="c-nome">${esc(m.nome)}${m.gold ? MEDALHA : ""}</span></a>`;
      }).join("") || `<p class="feed-vazio">Nenhum cliente cadastrado. Comece por Cadastros, depois instale o agente.</p>`;

      const ind = (valor, rotulo, tom, extra = "") => `<div class="ind" data-tom="${tom || ""}"><div class="ind-valor">${valor}${extra}</div><div class="ind-rotulo">${rotulo}</div></div>`;
      kpis.innerHTML = [
        ind(k.online, "equipamentos online", "", `<small>de ${k.equipamentos}</small>`),
        ind(k.offline, "offline agora", k.offline ? "ruim" : ""),
        ind(k.degradado, "com conexão degradada", k.degradado ? "atencao" : ""),
        ind(k.chamar_tecnico, "aguardando técnico", k.chamar_tecnico ? "ruim" : ""),
        ind(k.eventos_24h, "ocorrências nas últimas 24h"),
        ind(k.agentes_offline, "agentes sem contato", k.agentes_offline ? "ruim" : ""),
      ].join("");

      desenharSerie($("#serie"), d.serie);

      const maxT = Math.max(1, ...d.tipos.map((t) => t.n));
      $("#tipos").innerHTML = d.tipos.length ? d.tipos.map((t) => `
        <li><span>${esc(t.label)}</span><span class="b-n">${t.n}</span>
        <span class="b-trilho"><i style="width:${(100 * t.n / maxT).toFixed(1)}%"></i></span></li>`).join("")
        : `<li class="feed-vazio">Nenhuma ocorrência em 30 dias.</li>`;

      $("#top-clientes").innerHTML = d.top_clientes.map((c) => `
        <li><a href="/cliente/${encodeURIComponent(c.chave)}/"><span class="r-chave">${esc(c.chave)}</span>
        <span class="r-nome">${esc(c.nome)}</span><span class="r-n">${c.n}</span></a></li>`).join("")
        || `<li class="feed-vazio">Sem ocorrências no período.</li>`;

      desenharFeed($("#feed"), d.ultimos);
    });
  }

  function desenharSerie(el, s) {
    const totais = s.dias.map((_, i) => s.critico[i] + s.aviso[i] + s.info[i]);
    const max = Math.max(4, ...totais);
    const topo = Math.ceil(max / 4) * 4;
    const eixo = [topo, topo * 0.75, topo * 0.5, topo * 0.25, 0].map((v) => `<span>${Math.round(v)}</span>`).join("");
    const barras = s.dias.map((dia, i) => {
      const seg = (n, cls) => n ? `<span class="${cls}" style="height:${(100 * n / topo).toFixed(2)}%"></span>` : "";
      const rot = i % 5 === 0 || i === s.dias.length - 1 ? `<span class="dia-rotulo">${dia}</span>` : "";
      return `<div class="dia" data-i="${i}">${seg(s.critico[i], "s-critico")}${seg(s.aviso[i], "s-aviso")}${seg(s.info[i], "s-info")}${rot}</div>`;
    }).join("");
    el.innerHTML = `<div class="serie-eixo">${eixo}</div><div class="serie-area">${barras}</div>`;
    const area = el.querySelector(".serie-area");
    area.addEventListener("mousemove", (e) => {
      const dia = e.target.closest(".dia");
      let dica = area.querySelector(".dica");
      if (!dia) { dica && dica.remove(); return; }
      const i = +dia.dataset.i;
      if (!dica) { dica = document.createElement("div"); dica.className = "dica"; area.appendChild(dica); }
      dica.innerHTML = `<b>${s.dias[i]}: ${totais[i]} ocorrência${totais[i] === 1 ? "" : "s"}</b>${s.critico[i]} críticas, ${s.aviso[i]} avisos, ${s.info[i]} informativas`;
      const x = Math.min(dia.offsetLeft, area.clientWidth - dica.offsetWidth);
      dica.style.left = `${Math.max(0, x)}px`;
      dica.style.top = "-6px";
    });
    area.addEventListener("mouseleave", () => { const d = area.querySelector(".dica"); d && d.remove(); });
  }

  // ------------------------------------------------------------ cliente
  // Barra de saúde: um bloco por equipamento; o mouse (ou o foco) mostra o incidente aberto.
  let equipTicks = [], tickAtivo = null;

  function conteudoDicaTick(e) {
    const inc = e.incidente;
    const cab = `<b>${esc(e.nome)}</b><span class="dt-sub">${esc(e.ip)}, ${ROTULO_STATUS[e.status].toLowerCase()}</span>`;
    if (!inc) return `${cab}<p>Sem ocorrência aberta.</p>`;
    return `${cab}
      <p class="dt-titulo">${esc(inc.titulo)}</p>
      ${inc.detalhe ? `<p>${esc(inc.detalhe)}</p>` : ""}
      <p class="dt-decisao" data-d="${esc(inc.decisao)}"><b>${esc(inc.decisao_label)}${inc.prioridade ? `, prioridade ${inc.prioridade}` : ""}.</b>
        ${esc(inc.justificativa || "Aguardando análise.")}</p>
      <p class="dt-sub">Aberto ${ha(inc.desde)}${inc.ocorrencias > 1 ? `, ${inc.ocorrencias} falhas` : ""}${inc.analisado_por ? `, ${inc.analisado_por === "ia" ? "análise da IA" : "regra automática"}` : ""}</p>`;
  }

  function mostrarDicaTick(tick) {
    const barra = $("#saude-ticks");
    let dica = $(".dica-tick");
    if (!tick) { tickAtivo = null; if (dica) dica.remove(); return; }
    tickAtivo = tick.dataset.i;
    if (!dica) { dica = document.createElement("div"); dica.className = "dica dica-tick"; dica.setAttribute("role", "tooltip"); barra.parentElement.appendChild(dica); }
    dica.innerHTML = conteudoDicaTick(equipTicks[tickAtivo]);
    const caixa = barra.parentElement.getBoundingClientRect(), alvo = tick.getBoundingClientRect();
    const x = alvo.left - caixa.left + alvo.width / 2 - dica.offsetWidth / 2;
    dica.style.left = `${Math.max(0, Math.min(x, caixa.width - dica.offsetWidth))}px`;
    dica.style.top = `${alvo.bottom - caixa.top + 8}px`;
  }

  // Depois de cada atualização, a dica aberta passa a mostrar os dados novos.
  function atualizarDicaTick() {
    if (tickAtivo === null) return;
    mostrarDicaTick($(`#saude-ticks i[data-i="${tickAtivo}"]`));
  }

  function iniciarDicaTicks() {
    const barra = $("#saude-ticks");
    barra.addEventListener("mouseover", (ev) => { const t = ev.target.closest("i[data-i]"); if (t) mostrarDicaTick(t); });
    barra.addEventListener("mouseleave", () => mostrarDicaTick(null));
    barra.addEventListener("focusin", (ev) => mostrarDicaTick(ev.target.closest("i[data-i]")));
    barra.addEventListener("focusout", () => mostrarDicaTick(null));
  }

  function cliente() {
    const chave = document.body.dataset.chave;
    iniciarDicaTicks();
    $("#secoes").innerHTML = Array(4).fill('<div class="esqueleto" style="margin-bottom:8px"></div>').join("");
    // "toggle" não borbulha: escuta na fase de captura.
    $("#secoes").addEventListener("toggle", (ev) => {
      const id = ev.target.dataset && ev.target.dataset.id;
      if (!id || !ev.target.classList.contains("cam-mais")) return;
      if (ev.target.open) camsAbertas.add(id); else camsAbertas.delete(id);
    }, true);

    aoVivo(`/api/cliente/${encodeURIComponent(chave)}/`, (d) => {
      const ag = d.agentes[0];
      $("#cli-agente").dataset.online = ag ? String(ag.online) : "false";
      $("#cli-agente").innerHTML = ag ? `
        <i class="st st-${ag.online ? "online" : "offline"}"></i>
        <div><p class="ag-titulo">${!ag.online ? "Agente sem contato" : ag.pausado ? "Agente pausado: status congelados" : "Agente reportando"}
          <a class="ag-gerenciar" href="/agentes/">Gerenciar</a></p>
        <p class="ag-sub">${esc(ag.hostname || ag.nome)}${ag.ip_local ? `, ${esc(ag.ip_local)}` : ""}. Último contato ${ha(ag.ultimo_contato)}${ag.versao ? `, versão ${esc(ag.versao)}` : ""}</p></div>`
        : `<i class="st st-desconhecido"></i><div><p class="ag-titulo">Nenhum agente cadastrado</p><p class="ag-sub">Crie um em Cadastros para gerar o token.</p></div>`;

      const todos = d.secoes.flatMap((s) => s.itens);
      equipTicks = todos;
      $("#saude-ticks").innerHTML = todos.map((e, i) => `<i data-st="${e.status}" data-i="${i}" tabindex="0"
        ${e.incidente && e.incidente.chamar_tecnico ? 'data-tecnico="true"' : ""}
        aria-label="${esc(e.nome)}: ${ROTULO_STATUS[e.status]}${e.incidente ? `. ${esc(e.incidente.titulo)}` : ""}"></i>`).join("");
      atualizarDicaTick();
      const r = d.resumo;
      const partes = [`<strong>${r.online} de ${r.total}</strong> equipamentos online`];
      if (r.offline) partes.push(`${r.offline} offline`);
      if (r.degradado) partes.push(`${r.degradado} degradado${r.degradado > 1 ? "s" : ""}`);
      if (r.desconhecido) partes.push(`${r.desconhecido} sem dados`);
      $("#saude-texto").innerHTML = partes.join(", ") + (ag && !ag.online ? ". Status congelados desde o último contato do agente." : ".");

      $("#secoes").innerHTML = d.secoes.length ? d.secoes.map(desenharSecao).join("")
        : `<p class="feed-vazio">Nenhum equipamento cadastrado para este cliente.</p>`;
      desenharFeed($("#feed"), d.eventos, { mostrarCliente: false });
    });
  }

  function desenharSecao(s) {
    const corpo = s.chave === "camera"
      ? `<div class="cams">${s.itens.map(desenharCamera).join("")}</div>`
      : s.itens.map(desenharFicha).join("");
    return `<section class="secao" data-st="${s.status}" aria-label="${esc(s.titulo)}">
      <span class="secao-no" aria-hidden="true"></span>
      <div class="secao-cab"><h2>${esc(s.titulo)}</h2><span>${s.online} de ${s.itens.length} online</span></div>
      ${corpo}</section>`;
  }

  function estadoTexto(e) {
    if (!e.ultimo_check) return "Aguardando o primeiro teste do agente";
    return `<b>${ROTULO_STATUS[e.status]}</b>${e.status_desde ? ` ${ha(e.status_desde)}` : ""}. Testado ${ha(e.ultimo_check)}`;
  }

  function dados(pares) {
    return `<dl class="f-dados">${pares.filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `<div><dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join("")}</dl>`;
  }

  function desenharFicha(e) {
    const det = e.detalhes || {};
    const lat = e.latencia_ms != null ? `${e.latencia_ms.toFixed(e.latencia_ms < 10 ? 1 : 0)} ms` : "sem resposta";
    const pares = [
      ["IP", `${e.ip}${e.porta && e.porta !== 80 ? `:${e.porta}` : ""}`],
      ["Latência", lat],
      ["Perda de pacotes", e.perda_pct ? `${Math.round(e.perda_pct)}%` : null],
      ["Disponibilidade 24h", e.disponibilidade_24h != null ? `${e.disponibilidade_24h}%` : null],
      ["Firmware", e.firmware], ["Serial", e.serial],
      ["Depende de", e.depende_de],
    ];
    const extras = [];
    if (det.wan) pares.push(["Link", det.wan]);
    if (det.uptime) pares.push(["Ligado há", det.uptime]);
    if (det.so) pares.push(["Sistema", det.so]);
    if (det.cpu_pct != null) pares.push(["CPU", `${det.cpu_pct}%`]);
    if (typeof det.portas === "number") pares.push(["Portas ativas", `${det.portas_ativas ?? "?"} de ${det.portas}`]);
    if (det.portas_testadas) {
      const abertas = Object.entries(det.portas_testadas).filter(([, ok]) => ok).map(([p]) => p);
      pares.push(["Portas abertas", abertas.length ? abertas.join(", ") : "nenhuma"]);
    }
    if (det.memoria_pct != null) pares.push(["Memória", `${det.memoria_pct}%`]);
    if (det.temperatura_c != null) pares.push(["Temperatura", `${det.temperatura_c} °C`]);
    if (det.internet) pares.push(["Internet agora", det.internet.perda_pct >= 100 ? "sem resposta"
      : `${fmtMs(det.internet.latencia_ms)} até ${det.internet.alvo}${det.internet.perda_pct ? `, ${det.internet.perda_pct}% perda` : ""}`]);
    if (det.erro_coleta) pares.push(["Leitura da API", det.erro_coleta]);
    if (det.poe_watts != null) pares.push(["Consumo PoE", `${det.poe_watts} W`]);
    if (det.zonas) pares.push(["Zonas", det.zonas]);
    if (det.armado != null) pares.push(["Situação", det.armado ? "Armada" : "Desarmada"]);
    if (det.sip_registrado != null) pares.push(["SIP", det.sip_registrado ? `Registrado${det.ramal ? `, ramal ${det.ramal}` : ""}` : "Não registrado"]);

    if (det.canais && det.canais.length) {
      const off = det.canais.filter((c) => !c.online).length;
      extras.push(`<div><h3>Canais: ${det.canais.length - off} de ${det.canais.length} com sinal</h3>
        <div class="canais">${det.canais.map((c) => `<i data-on="${c.online !== false}" title="Canal ${esc(c.id)} ${esc(c.nome || "")}: ${c.online !== false ? "com sinal" : "sem sinal"}"></i>`).join("")}</div></div>`);
    }
    (det.hdds || []).forEach((h) => {
      const ok = (h.status || "ok") === "ok";
      const usado = h.capacidade_gb && h.livre_gb != null ? 100 * (1 - h.livre_gb / h.capacidade_gb) : null;
      extras.push(`<div class="hd" data-ok="${ok}"><h3>HD ${esc(h.id)}</h3>
        <p>${ok ? "Funcionando" : `Problema: ${esc(h.status)}`}${h.capacidade_gb ? `, ${(h.capacidade_gb / 1000).toFixed(h.capacidade_gb >= 1000 ? 0 : 1)} TB` : ""}</p>
        ${usado != null ? `<div class="hd-trilho"><i style="width:${usado.toFixed(0)}%"></i></div>` : ""}</div>`);
    });

    return `<article class="ficha" data-st="${e.status}"${e.incidente && e.incidente.chamar_tecnico ? ' data-tecnico="true"' : ""}>
      <div>
        <p class="f-nome"><i class="st st-${e.status}"></i>${esc(e.nome)}</p>
        <p class="f-modelo">${esc([e.fabricante === "Outro" ? "" : e.fabricante, e.modelo].filter(Boolean).join(" "))}${e.critico ? ", marcado como crítico" : ""}</p>
        <p class="f-estado">${estadoTexto(e)}</p>
      </div>
      ${dados(pares)}
      <div class="f-grafico">${sparkline(e.sparkline, 150, 44)}<p>Latência na última hora</p></div>
      ${extras.length ? `<div class="f-extra">${extras.join("")}</div>` : ""}
      ${det.links && det.links.length ? blocoLinks(det.links, det.netwatch) : ""}
    </article>`;
  }

  // ------------------------------------------------------------ links de internet
  const fmtMs = (v) => (v == null ? "?" : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`);
  function fmtBps(bps) {
    if (bps == null) return "?";
    if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
    if (bps >= 1e6) return `${(bps / 1e6).toFixed(bps >= 1e8 ? 0 : 1)} Mbps`;
    return `${Math.round(bps / 1e3)} kbps`;
  }
  const ROTULO_LINK = { online: "Online", instavel: "Instável", sem_internet: "Sem internet", offline: "Porta sem sinal" };
  const ST_LINK = { online: "online", instavel: "degradado", sem_internet: "offline", offline: "offline" };

  function medidor(rotulo, bps, contratado, uso) {
    const pct = uso != null ? Math.min(100, uso) : null;
    const nivel = pct == null ? "" : pct >= 90 ? "alto" : pct >= 70 ? "medio" : "";
    return `<div class="medidor" data-nivel="${nivel}">
      <p><span>${rotulo}</span><b>${fmtBps(bps)}</b>${contratado ? `<span class="m-de">de ${contratado} Mbps</span>` : ""}</p>
      ${pct != null ? `<div class="m-trilho"><i style="width:${Math.max(pct, 1).toFixed(1)}%"></i></div>` : ""}
    </div>`;
  }

  function graficoTrafego(h, contratadoMbps, w = 220, alt = 46) {
    const rx = h.rx || [], tx = h.tx || [], ok = h.ok || [];
    if (rx.length < 2) return `<svg viewBox="0 0 ${w} ${alt}" aria-hidden="true"><line x1="0" y1="${alt - 1}" x2="${w}" y2="${alt - 1}" stroke="var(--linha)" stroke-dasharray="3 3"/></svg>`;
    const teto = Math.max(contratadoMbps ? contratadoMbps * 1e6 : 0, ...rx.map((v) => v || 0), ...tx.map((v) => v || 0), 1e6);
    const passo = w / (rx.length - 1);
    const y = (v) => (alt - 1) - ((v || 0) / teto) * (alt - 4);
    const pts = (serie) => serie.map((v, i) => `${(i * passo).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const quedas = ok.map((b, i) => b ? "" : `<rect class="queda-grafico" x="${Math.max(0, i * passo - passo / 2).toFixed(1)}" y="0" width="${Math.max(passo, 2).toFixed(1)}" height="${alt}"/>`).join("");
    const limite = contratadoMbps ? `<line class="g-limite" x1="0" x2="${w}" y1="${y(contratadoMbps * 1e6 * 0.9).toFixed(1)}" y2="${y(contratadoMbps * 1e6 * 0.9).toFixed(1)}"/>` : "";
    return `<svg viewBox="0 0 ${w} ${alt}" preserveAspectRatio="none" aria-hidden="true">${quedas}${limite}
      <polygon class="g-rx" points="0,${alt} ${pts(rx)} ${w},${alt}"/><polyline class="g-tx" points="${pts(tx)}"/></svg>`;
  }

  function blocoLinks(links, netwatch) {
    const linhas = links.map((l) => {
      const st = l.status || "online";
      const papel = l.principal ? "Principal" : (st === "online" || st === "instavel") ? "Reserva" : "Fora do ar";
      const teste = l.internet_ping || l.gateway_ping;
      const testeTxt = !teste ? "sem teste" : teste.perda_pct >= 100 ? "sem resposta"
        : `${fmtMs(teste.latencia_ms)}${teste.perda_pct ? `, ${teste.perda_pct}% perda` : ""}`;
      const fonte = l.internet_ping ? `internet por este link` : `gateway do provedor`;
      const info = [
        l.interface, l.tipo, l.ip,
        l.taxa_porta_mbps ? `porta ${l.taxa_porta_mbps >= 1000 ? `${l.taxa_porta_mbps / 1000} G` : `${l.taxa_porta_mbps} M`}${l.full_duplex === false ? " half-duplex" : ""}` : "",
      ].filter(Boolean).map((t) => `<span>${esc(t)}</span>`).join("");
      return `<li class="link" data-st="${ST_LINK[st] || "desconhecido"}">
        <div class="l-id">
          <p class="l-nome"><i class="st st-${ST_LINK[st] || "desconhecido"}"></i>${esc(l.nome || l.interface)}
            <span class="l-papel" data-papel="${papel === "Principal" ? "principal" : papel === "Reserva" ? "reserva" : "fora"}">${papel}</span></p>
          <p class="l-info">${info}</p>
          <p class="l-estado"><b>${ROTULO_LINK[st] || st}</b>${l.status_desde ? ` ${ha(l.status_desde)}` : ""}</p>
        </div>
        <div class="l-trafego">
          ${medidor("Download", l.rx_bps, l.download_mbps, l.uso_down_pct)}
          ${medidor("Upload", l.tx_bps, l.upload_mbps, l.uso_up_pct)}
        </div>
        <dl class="l-dados">
          <div><dt>Latência (${fonte})</dt><dd>${testeTxt}</dd></div>
          <div><dt>Disponibilidade 24h</dt><dd>${l.disponibilidade_24h != null ? `${l.disponibilidade_24h}%` : "?"}</dd></div>
          <div><dt>Quedas da porta</dt><dd>${l.quedas_porta ?? "?"}${l.pppoe_uptime ? `, PPPoE há ${esc(l.pppoe_uptime)}` : ""}</dd></div>
        </dl>
        <div class="l-grafico">${graficoTrafego(l.historico || {}, l.download_mbps)}<p>Tráfego na última hora${l.download_mbps ? ", linha em 90% do contratado" : ""}</p></div>
      </li>`;
    }).join("");
    const nw = (netwatch || []).length ? `<p class="l-netwatch">Netwatch no roteador:
      ${netwatch.map((n) => `<span data-up="${n.status === "up"}">${esc(n.comentario || n.host)} ${n.status === "up" ? "respondendo" : "fora"}</span>`).join("")}</p>` : "";
    const fora = links.filter((l) => l.status === "offline" || l.status === "sem_internet").length;
    return `<section class="f-links" aria-label="Links de internet">
      <h3>Links de internet: ${links.length - fora} de ${links.length} funcionando</h3>
      <ul>${linhas}</ul>${nw}</section>`;
  }

  // Detalhes abertos sobrevivem ao redesenho do polling.
  const camsAbertas = new Set();

  // "2.860.00IB000.0.T,build:2024-10-12" (Dahua) / "V5.7.3 build 220112" (Hikvision)
  function separarFirmware(fw) {
    const m = /^(.*?)[,\s]+build[:\s]*(.+)$/i.exec(fw || "");
    if (!m) return [fw, null];
    const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(m[2]);
    return [m[1], iso ? `${iso[3]}/${iso[2]}/${iso[1]}` : m[2]];
  }

  function desenharCamera(e) {
    const det = e.detalhes || {};
    const lat = e.latencia_ms != null ? `${e.latencia_ms.toFixed(e.latencia_ms < 10 ? 1 : 0)} ms` : "";
    const modelo = [e.fabricante === "Outro" ? "" : e.fabricante, e.modelo].filter(Boolean).join(" ");
    const [versao, build] = separarFirmware(e.firmware);
    const pares = [
      ["Firmware", versao], ["Data do firmware", build],
      ["Serial", e.serial], ["MAC", e.mac],
      ["Disponibilidade 24h", e.disponibilidade_24h != null ? `${e.disponibilidade_24h}%` : null],
      ["Perda de pacotes", e.perda_pct ? `${Math.round(e.perda_pct)}%` : null],
      ["Porta web", e.porta && e.porta !== 80 ? e.porta : null],
      ["Depende de", e.depende_de],
      ["Último teste", e.ultimo_check ? ha(e.ultimo_check) : "aguardando o agente"],
    ];
    return `<article class="cam" data-st="${e.status}"${e.incidente && e.incidente.chamar_tecnico ? ' data-tecnico="true"' : ""}>
      <p class="cam-nome"><i class="st st-${e.status}"></i>${esc(e.nome)}</p>
      <p class="cam-modelo" title="${esc(modelo)}">${esc(modelo) || "Modelo ainda não lido"}</p>
      <p class="cam-sub"><span>${esc(e.ip)}</span><span>${lat}</span></p>
      ${det.erro_coleta ? `<p class="cam-aviso" title="${esc(det.erro_coleta)}">Leitura da API: ${esc(det.erro_coleta)}</p>` : ""}
      ${e.status === "offline" ? `<p class="cam-off">Sem resposta ${ha(e.status_desde)}</p>` : sparkline(e.sparkline, 180, 26)}
      <details class="cam-mais" data-id="${e.id}"${camsAbertas.has(String(e.id)) ? " open" : ""}>
        <summary>Detalhes</summary>${dados(pares)}
      </details>
    </article>`;
  }

  // ------------------------------------------------------------ agentes
  function duracao(iso) {
    // "há 3 h 12 min" -> "3 h 12 min"
    return ha(iso).replace(/^há /, "");
  }

  function situacaoAgente(a) {
    if (!a.ultimo_contato) return ["desconhecido", "Nunca se comunicou", "Instale o agente e confira o token no agente.ini."];
    if (!a.online) return ["offline", `Sem contato ${ha(a.ultimo_contato)}`,
      "Pode ser internet ou energia do local, a máquina desligada ou o agente fechado."];
    if (a.pausado) return ["degradado", "Pausado", "Comunicando, mas sem testar nenhum equipamento. Os status estão congelados."];
    return ["online", "Comunicando", `Último contato ${ha(a.ultimo_contato)}.`];
  }

  function linhaComando(a) {
    if (a.comando) {
      return `<p class="ag-cmd" data-pendente="true">
        <b>${esc(a.comando_label)}</b> pedido${a.comando_pedido_por ? ` por ${esc(a.comando_pedido_por)}` : ""} ${ha(a.comando_pedido_em)}.
        ${a.online ? `Aguardando o próximo contato do agente (até ${a.intervalo_segundos} s).` : "O agente está sem contato: o comando fica na fila até ele voltar."}
        ${document.body.dataset.staff ? `<button type="button" class="ag-link" data-cmd="cancelar" data-id="${a.id}">Cancelar</button>` : ""}</p>`;
    }
    if (a.comando_entregue) {
      return `<p class="ag-cmd">Último comando: <b>${esc(a.comando_entregue_label)}</b>, entregue ${ha(a.comando_entregue_em)}.</p>`;
    }
    return "";
  }

  function botoesAgente(a) {
    if (!document.body.dataset.staff) return "";
    if (!a.aceita_comandos) {
      return `<p class="ag-aviso">A versão ${esc(a.versao || "?")} deste agente não recebe comandos.
        Copie os arquivos novos para a máquina e reinicie o agente uma última vez à mão; a partir da 1.0.3 dá para controlar por aqui.</p>`;
    }
    const ocupado = a.comando ? " disabled" : "";
    return `<div class="ag-botoes">
      <button type="button" class="ag-botao" data-cmd="recarregar" data-id="${a.id}"${ocupado}
        title="Busca equipamentos e senhas na hora e lê os detalhes de novo">Recarregar cadastro</button>
      ${a.pausado
        ? `<button type="button" class="ag-botao" data-cmd="retomar" data-id="${a.id}"${ocupado}>Retomar testes</button>`
        : `<button type="button" class="ag-botao" data-cmd="pausar" data-id="${a.id}"${ocupado}
            title="Para de acessar os equipamentos (ex.: câmera bloqueando por senha errada)">Pausar testes</button>`}
      <button type="button" class="ag-botao ag-botao-forte" data-cmd="reiniciar" data-id="${a.id}"${ocupado}
        title="Reinicia o programa do agente na máquina do cliente">Reiniciar agente</button>
    </div>`;
  }

  function linhaEquipamentos(a) {
    const eq = a.equipamentos;
    if (!eq.offline && !eq.degradado) return "";
    const partes = [];
    if (eq.offline) partes.push(`<b class="eq-off">${eq.offline} offline</b>: ${esc(eq.nomes_offline.join(", "))}`);
    if (eq.degradado) partes.push(`<b class="eq-deg">${eq.degradado} degradado${eq.degradado > 1 ? "s" : ""}</b>: ${esc(eq.nomes_degradado.join(", "))}`);
    const quem = a.online ? "O agente está ok; o problema é nos equipamentos." : "Status do último contato do agente.";
    return `<p class="ag-equip">${quem} ${partes.join("; ")}.
      <a href="/cliente/${encodeURIComponent(a.cliente.chave)}/">Ver cliente</a></p>`;
  }

  function cartaoAgente(a) {
    const [st, titulo, sub] = situacaoAgente(a);
    const eq = a.equipamentos;
    const pares = [
      ["Máquina", a.hostname], ["IP local", a.ip_local], ["IP público", a.ip_publico],
      ["Sistema", a.sistema], ["Versão", a.versao],
      ["Rodando há", a.iniciado_em && a.online ? duracao(a.iniciado_em) : null],
      ["Equipamentos online", `${eq.online} de ${eq.total}`], ["Checagem a cada", `${a.intervalo_segundos} s`],
    ];
    return `<article class="ag-cartao" data-st="${st}">
      <div class="ag-id">
        <p class="ag-cli"><a href="/cliente/${encodeURIComponent(a.cliente.chave)}/"><span>${esc(a.cliente.chave)}</span> ${esc(a.cliente.nome)}</a></p>
        <p class="ag-nome">${esc(a.nome)}</p>
        <p class="ag-situacao"><i class="st st-${st}"></i><b>${titulo}</b></p>
        <p class="ag-sub">${sub}</p>
        ${linhaEquipamentos(a)}
      </div>
      ${dados(pares)}
      <div class="ag-acoes">${botoesAgente(a)}${linhaComando(a)}</div>
    </article>`;
  }

  async function enviarComando(id, comando) {
    const csrf = $("input[name=csrfmiddlewaretoken]");
    const corpo = new URLSearchParams({ comando });
    const resp = await fetch(`/api/agentes/${id}/comando/`, {
      method: "POST", body: corpo, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf ? csrf.value : "" },
    });
    const dados = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(dados.erro || `HTTP ${resp.status}`);
  }

  function agentes() {
    const lista = $("#agentes");
    lista.innerHTML = Array(2).fill('<div class="esqueleto" style="margin-bottom:8px"></div>').join("");
    let recarregar = () => {};

    lista.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-cmd]");
      if (!b) return;
      const cmd = b.dataset.cmd;
      if (cmd === "reiniciar" && !confirm("Reiniciar o agente? Ele fica alguns segundos sem testar os equipamentos.")) return;
      b.disabled = true;
      try {
        await enviarComando(b.dataset.id, cmd);
        recarregar();
      } catch (e) {
        alert(`Não foi possível enviar o comando: ${e.message}`);
        b.disabled = false;
      }
    });

    recarregar = aoVivo("/api/agentes/", (d) => {
      const sem = d.agentes.filter((a) => !a.online).length;
      const pausados = d.agentes.filter((a) => a.online && a.pausado).length;
      const partes = [`<strong>${d.agentes.length - sem} de ${d.agentes.length}</strong> agentes comunicando`];
      if (sem) partes.push(`${sem} sem contato`);
      if (pausados) partes.push(`${pausados} pausado${pausados > 1 ? "s" : ""}`);
      $("#agentes-resumo").innerHTML = partes.join(", ");
      lista.innerHTML = d.agentes.map(cartaoAgente).join("")
        || `<p class="feed-vazio">Nenhum agente cadastrado. Crie um em Cadastros, no cliente.</p>`;
    });
  }

  // ------------------------------------------------------------ início
  document.addEventListener("DOMContentLoaded", () => {
    iniciarBusca();
    iniciarRelogio();
    const pagina = document.body.dataset.page;
    if (pagina === "dashboard") dashboard();
    if (pagina === "cliente") cliente();
    if (pagina === "agentes") agentes();
  });
})();
