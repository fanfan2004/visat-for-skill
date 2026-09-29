(function () {
  "use strict";

  const DATA_URL = "data/stock-descriptions.csv";
  const SECTOR_ORDER = [
    "Information Technology",
    "Industry",
    "Energy",
    "Financial",
    "ETF"
  ];

  const SECTOR_COLORS = {
    "Information Technology": "#1f77b4",
    "Industry": "#ff7f0e",
    "Energy": "#2ca02c",
    "Financial": "#d62728",
    "ETF": "#9467bd"
  };

  const state = {
    nodes: [],
    selectedTicker: null,
    simulation: null,
    svg: null,
    bubbleGroup: null,
    width: 0,
    height: 0,
    sectorCenters: {}
  };

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    try {
      const raw = await d3.csv(DATA_URL);
      state.nodes = prepareNodes(raw);
    } catch (err) {
      console.error("Failed to load stock data:", err);
      document.getElementById("chart-panel").innerHTML =
        '<p style="color:#a00;padding:12px;">Failed to load data: ' + err.message + "</p>";
      return;
    }

    renderTable(state.nodes);
    renderChart(state.nodes);
  }

  function prepareNodes(raw) {
    return raw.map((row) => {
      const ticker = row.ticker;
      const sector = row.sector;
      const name = row["full name"];
      const marketCapRaw = row.marketCap;
      const marketCap = marketCapRaw && marketCapRaw !== ""
        ? parseFloat(marketCapRaw)
        : null;
      const isEtf = sector === "ETF";
      return {
        ticker: ticker,
        sector: sector,
        name: name,
        marketCap: marketCap,
        isEtf: isEtf
      };
    });
  }

  function formatMarketCap(value) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    const abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (value / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (value / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (value / 1e3).toFixed(2) + "K";
    return String(value);
  }

  function renderTable(nodes) {
    const tbody = document.querySelector("#stock-table tbody");
    tbody.innerHTML = "";
    nodes.forEach((n) => {
      const tr = document.createElement("tr");
      tr.dataset.ticker = n.ticker;

      const tdTicker = document.createElement("td");
      tdTicker.className = "ticker-cell";
      tdTicker.textContent = n.ticker;
      tr.appendChild(tdTicker);

      const tdName = document.createElement("td");
      tdName.textContent = n.name;
      tr.appendChild(tdName);

      const tdSector = document.createElement("td");
      const sectorWrap = document.createElement("span");
      sectorWrap.className = "sector-cell";
      const dot = document.createElement("span");
      dot.className = "sector-dot";
      dot.style.background = SECTOR_COLORS[n.sector] || "#888";
      sectorWrap.appendChild(dot);
      sectorWrap.appendChild(document.createTextNode(n.sector));
      tdSector.appendChild(sectorWrap);
      tr.appendChild(tdSector);

      const tdMcap = document.createElement("td");
      tdMcap.className = "mcap-cell";
      tdMcap.textContent = formatMarketCap(n.marketCap);
      tr.appendChild(tdMcap);

      tr.addEventListener("click", () => selectTicker(n.ticker, { source: "table" }));
      tbody.appendChild(tr);
    });
  }

  function renderChart(nodes) {
    const container = document.getElementById("chart-panel");
    container.style.position = "relative";

    const tooltip = document.createElement("div");
    tooltip.id = "tooltip";
    tooltip.setAttribute("role", "tooltip");
    container.appendChild(tooltip);

    const legend = document.createElement("div");
    legend.className = "legend";
    SECTOR_ORDER.forEach((sector) => {
      const item = document.createElement("div");
      item.className = "legend-item";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.background = SECTOR_COLORS[sector];
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(sector));
      legend.appendChild(item);
    });
    container.appendChild(legend);

    const svgEl = document.getElementById("bubble-chart");
    const svg = d3.select(svgEl);
    state.svg = svg;
    state.bubbleGroup = svg.append("g").attr("class", "bubbles");

    const resize = () => {
      const rect = svgEl.getBoundingClientRect();
      state.width = rect.width;
      state.height = rect.height;
      svg.attr("viewBox", `0 0 ${rect.width} ${rect.height}`);
      if (state.simulation) {
        state.simulation.force("center", d3.forceCenter(rect.width / 2, rect.height / 2));
        recomputeSectorCenters(rect.width, rect.height);
        state.simulation.alpha(0.6).restart();
      }
    };
    resize();
    window.addEventListener("resize", resize);

    recomputeSectorCenters(state.width, state.height);

    const radiusScale = d3.scaleSqrt()
      .domain([0, d3.max(nodes, (n) => n.marketCap || 0) || 1])
      .range([10, 30]);

    const ETF_RADIUS = 14;

    nodes.forEach((n) => {
      n.radius = n.isEtf
        ? ETF_RADIUS
        : Math.max(9, radiusScale(n.marketCap || 0));
    });

    const simulation = d3.forceSimulation(nodes)
      .force("collide", d3.forceCollide().radius((d) => d.radius + 2).iterations(4))
      .force("x", d3.forceX((d) => state.sectorCenters[d.sector].x).strength(0.35))
      .force("y", d3.forceY((d) => state.sectorCenters[d.sector].y).strength(0.35))
      .force("center", d3.forceCenter(state.width / 2, state.height / 2))
      .stop();

    for (let i = 0; i < 500; i++) simulation.tick();
    runSimulation(simulation, nodes);

    const groups = state.bubbleGroup.selectAll("g.bubble-node")
      .data(nodes, (d) => d.ticker)
      .enter()
      .append("g")
      .attr("class", "bubble-node")
      .attr("data-ticker", (d) => d.ticker);

    groups.append("circle")
      .attr("class", (d) => "bubble" + (d.isEtf ? " is-etf" : ""))
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => SECTOR_COLORS[d.sector] || "#888")
      .on("mouseover", (event, d) => onBubbleHover(event, d, tooltip))
      .on("mousemove", (event, d) => onBubbleHover(event, d, tooltip))
      .on("mouseout", () => onBubbleOut(tooltip))
      .on("click", (event, d) => selectTicker(d.ticker, { source: "bubble" }));

    groups.append("text")
      .attr("class", "bubble-label")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .text((d) => d.ticker);

    groups.attr("transform", (d) => `translate(${d.x},${d.y})`);

    simulation.on("tick", () => {
      nodes.forEach((d) => {
        d.x = clamp(d.x, d.radius, state.width - d.radius);
        d.y = clamp(d.y, d.radius, state.height - d.radius);
      });
      groups.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });
  }

  function recomputeSectorCenters(width, height) {
    const sectors = SECTOR_ORDER.filter((s) => s in SECTOR_COLORS);
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.22;
    const angleOffset = -Math.PI / 2;
    sectors.forEach((sector, i) => {
      const angle = angleOffset + (2 * Math.PI * i) / sectors.length;
      state.sectorCenters[sector] = {
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius
      };
    });
  }

  function runSimulation(simulation, nodes) {
    simulation.nodes(nodes);
    simulation.alpha(1).restart();
    for (let i = 0; i < 300; i++) simulation.tick();
    simulation.stop();
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function onBubbleHover(event, d, tooltip) {
    if (d.isEtf) {
      onBubbleOut(tooltip);
      return;
    }
    const rect = document.getElementById("chart-panel").getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    tooltip.innerHTML =
      '<div class="tt-ticker">' + escapeHtml(d.ticker) + "</div>" +
      '<div class="tt-name">' + escapeHtml(d.name) + "</div>" +
      '<div class="tt-sector">Sector: ' + escapeHtml(d.sector) + "</div>";
    tooltip.style.left = x + "px";
    tooltip.style.top = y + "px";
    tooltip.classList.add("visible");
  }

  function onBubbleOut(tooltip) {
    tooltip.classList.remove("visible");
  }

  function selectTicker(ticker, opts) {
    const source = (opts && opts.source) || "table";
    state.selectedTicker = ticker;

    d3.selectAll("#bubble-chart circle.bubble").classed("is-selected", (d) => d.ticker === ticker);
    d3.selectAll("#bubble-chart circle.bubble").each(function (d) {
      if (d.ticker === ticker) {
        const node = this.parentNode;
        if (node && node.parentNode) {
          node.parentNode.appendChild(node);
        }
      }
    });

    const rows = document.querySelectorAll("#stock-table tbody tr");
    rows.forEach((tr) => {
      if (tr.dataset.ticker === ticker) {
        tr.classList.add("is-selected");
        if (source === "bubble") {
          tr.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
        }
      } else {
        tr.classList.remove("is-selected");
      }
    });
  }

  function escapeHtml(s) {
    if (s === undefined || s === null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
})();
