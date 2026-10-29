// Cadastro em lote: mostra a seção só para as categorias que aceitam lote
// e exibe a faixa de nomes/IPs que será criada.
// Mesma lista de CATEGORIAS_LOTE em forms.py.
const CATEGORIAS_LOTE = ["camera", "gravador", "servidor", "alarme", "acesso", "facial"];

document.addEventListener("DOMContentLoaded", () => {
  const caixa = document.querySelector("fieldset.lote-cameras");
  const categoria = document.getElementById("id_categoria");
  if (!caixa || !categoria) return;

  const nome = document.getElementById("id_nome");
  const ip = document.getElementById("id_ip");
  const qtd = document.getElementById("id_quantidade");
  const inicio = document.getElementById("id_numero_inicial");

  const previa = document.createElement("p");
  previa.className = "help";
  previa.style.margin = "8px 10px";
  caixa.appendChild(previa);

  function rotulo(n, total) {
    const largura = Math.max(2, String(total).length);
    const base = nome.value.trim();
    const sep = /[-_.]$/.test(base) ? "" : " ";
    return `${base}${sep}${String(n).padStart(largura, "0")}`;
  }

  function atualizar() {
    caixa.style.display = CATEGORIAS_LOTE.includes(categoria.value) ? "" : "none";
    const n = parseInt(qtd.value, 10) || 1;
    const partes = ip.value.trim().split(".");
    if (n <= 1) { previa.textContent = ""; return; }
    if (partes.length !== 4 || partes.some((p) => !/^\d+$/.test(p) || +p > 255)) {
      previa.textContent = "Informe um IPv4 no campo IP para ver a faixa.";
      return;
    }
    const base = partes.slice(0, 3).join(".");
    const primeiro = +partes[3];
    const ultimo = primeiro + n - 1;
    if (ultimo > 254) {
      previa.textContent = `A faixa passaria de ${base}.254 (máximo ${255 - primeiro} equipamentos).`;
      return;
    }
    const i0 = inicio.value === "" ? 1 : parseInt(inicio.value, 10) || 0;
    const fim = i0 + n - 1;
    previa.textContent = `Serão criados ${n} equipamentos: “${rotulo(i0, fim)}” (${base}.${primeiro}) `
      + `até “${rotulo(fim, fim)}” (${base}.${ultimo}).`;
  }

  [categoria, nome, ip, qtd, inicio].forEach((el) => {
    el.addEventListener("input", atualizar);
    el.addEventListener("change", atualizar);
  });
  atualizar();
});
