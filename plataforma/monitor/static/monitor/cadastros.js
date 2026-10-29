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
