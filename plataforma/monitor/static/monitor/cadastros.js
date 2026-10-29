// Telas de cadastro: filtros que se aplicam sozinhos, opções que dependem do
// cliente escolhido, links de internet só para gateway e o agente.ini copiável.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("select[data-auto]").forEach((sel) => {
    sel.addEventListener("change", () => sel.form.requestSubmit());
  });

  document.querySelectorAll("form[data-confirmar]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!confirm(form.dataset.confirmar)) e.preventDefault();
    });
  });

  // Agente e "depende de": só os itens do cliente escolhido.
  const cliente = document.getElementById("id_cliente");
  const porCliente = document.querySelectorAll("select[data-por-cliente]");
  function filtrarPorCliente() {
    porCliente.forEach((sel) => {
      for (const opcao of sel.options) {
        if (!opcao.value) continue;
        const ok = !cliente.value || opcao.dataset.cliente === cliente.value;
        opcao.hidden = !ok;
        opcao.disabled = !ok;
        if (!ok && opcao.selected) sel.value = "";
      }
    });
  }
  if (cliente && porCliente.length) {
    cliente.addEventListener("change", filtrarPorCliente);
    filtrarPorCliente();
  }

  // Links de internet: aparecem para gateway (ou se o equipamento já tiver algum).
  const categoria = document.getElementById("id_categoria");
  const secaoLinks = document.querySelector(".secao-links");
  if (categoria && secaoLinks) {
    const mostrar = () => {
      secaoLinks.hidden = categoria.value !== "gateway" && !secaoLinks.dataset.temLinks;
    };
    categoria.addEventListener("change", mostrar);
    mostrar();
  }

  document.querySelectorAll("[data-novo-link]").forEach((botao) => {
    const prefixo = botao.dataset.novoLink;
    const total = document.getElementById(`id_${prefixo}-TOTAL_FORMS`);
    const modelo = document.getElementById("link-vazio");
    const lista = document.getElementById("links-lista");
    botao.addEventListener("click", () => {
      const n = parseInt(total.value, 10);
      lista.insertAdjacentHTML("beforeend", modelo.innerHTML.replace(/__prefix__/g, n));
      total.value = n + 1;
      lista.lastElementChild.querySelector("input:not([type=hidden])")?.focus();
    });
  });

  // Situação da conexão do agente, atualizada enquanto a página está aberta
  // (para ver o agente aparecer logo depois de rodar o instalador).
  const conexao = document.querySelector("[data-conexao-agente]");
  if (conexao) {
    const texto = conexao.querySelector("[data-conexao-texto]");
    const id = Number(conexao.dataset.conexaoAgente);
    const atualizar = async () => {
      try {
        const r = await fetch("/api/agentes/", { headers: { Accept: "application/json" } });
        if (!r.ok) return;
        const a = (await r.json()).agentes.find((x) => x.id === id);
        if (!a) {
          conexao.dataset.st = "desconhecido";
          texto.textContent = "Agente ou cliente inativo: a plataforma não aceita a conexão dele.";
          return;
        }
        const quando = a.ultimo_contato ? new Date(a.ultimo_contato) : null;
        if (a.online) {
          const seg = Math.max(0, Math.round((Date.now() - quando) / 1000));
          const maquina = [a.hostname, a.versao && `versão ${a.versao}`].filter(Boolean).join(", ");
          conexao.dataset.st = "online";
          texto.textContent = `Conectado. Último contato há ${seg < 60 ? `${seg} s` : `${Math.round(seg / 60)} min`}`
            + (maquina ? ` (${maquina}).` : ".");
        } else if (quando) {
          conexao.dataset.st = "offline";
          texto.textContent = `Sem contato desde ${quando.toLocaleString("pt-BR")}.`;
        } else {
          conexao.dataset.st = "desconhecido";
          texto.textContent = "Aguardando a primeira conexão do agente.";
        }
      } catch { /* sem rede: tenta de novo no próximo ciclo */ }
    };
    atualizar();
    setInterval(atualizar, 5000);
  }

  // Equipamentos dentro do cliente: filtro rápido que esconde os grupos sem resultado.
  const filtroEquip = document.querySelector("[data-filtrar-equip]");
  if (filtroEquip) {
    const vazio = document.querySelector("[data-filtro-vazio]");
    filtroEquip.addEventListener("input", () => {
      const termo = filtroEquip.value.trim().toLowerCase();
      let algum = false;
      document.querySelectorAll("[data-grupo]").forEach((grupo) => {
        let n = 0;
        grupo.querySelectorAll("tr[data-busca]").forEach((tr) => {
          tr.hidden = termo && !tr.dataset.busca.includes(termo);
          if (!tr.hidden) n += 1;
        });
        grupo.hidden = n === 0;
        algum ||= n > 0;
      });
      vazio.hidden = algum;
    });
  }

  document.querySelectorAll("[data-copiar]").forEach((botao) => {
    botao.addEventListener("click", async () => {
      const alvo = document.getElementById(botao.dataset.copiar);
      try {
        await navigator.clipboard.writeText(alvo.textContent);
        botao.textContent = "Copiado";
      } catch {
        // Sem permissão de área de transferência (HTTP, navegador antigo): seleciona o texto.
        const faixa = document.createRange();
        faixa.selectNodeContents(alvo);
        getSelection().removeAllRanges();
        getSelection().addRange(faixa);
        botao.textContent = "Selecionado: Ctrl+C";
      }
      setTimeout(() => { botao.textContent = "Copiar"; }, 2500);
    });
  });
});
