(function () {
  "use strict";

  const SECTOR_COLORS = {
    "Information Technology": "#3b82f6",
    "Industry": "#ef4444",
    "Energy": "#f59e0b",
    "Financial": "#10b981",
    "ETF": "#8b5cf6"
  };

  const CSV_URL = "data/stock-descriptions.csv";

  const state = {
    stocks: [],
    sectors: [],
    sectorCenters: {},
    selectedTicker: null,
    simulation: null,
    width: 0,
    height: 0
  };

  const svg = d3.select("#bubble-chart");
  const tooltip = d3.select("#tooltip");
  const legendEl = document.getElementById("legend");
  const tableBody = document.querySelector("#stock-table tbody");

  const bubblesGroup = svg.append("g").attr("class", "bubbles");
  const labelsGroup = svg.append("g").attr("class", "labels");

  function formatMarketCap(value) {
    if (value === null || value === undefined || value === "" ||
        (typeof value === "number" && !isFinite(value))) {
      return { text: "N/A", isNA: true };
    }
    const num = +value;
    if (!isFinite(num) || num <= 0) {
      return { text: "N/A", isNA: true };
    }
    const abs = Math.abs(num);
    if (abs >= 1e12) return { text: (num / 1e12).toFixed(2) + "T", isNA: false };
    if (abs >= 1e9)  return { text: (num / 1e9).toFixed(2) + "B",  isNA: false };
    if (abs >= 1e6)  return { text: (num / 1e6).toFixed(2) + "M",  isNA: false };
    if (abs >= 1e3)  return { text: (num / 1e3).toFixed(2) + "K",  isNA: false };
    return { text: num.toFixed(0), isNA: false };
  }

  function isETF(d) {
    return d.sector === "ETF" || d.marketCap === null ||
           d.marketCap === undefined || d.marketCap === "";
  }

  function parseRow(d) {
    const mc = (d.marketCap === "" || d.marketCap === undefined) ? null : +d.marketCap;
    return {
      ticker: d.ticker,
      sector: d.sector,
      fullName: d["full name"] || d.fullName || d.full_name || "",
      marketCap: (mc === null || isNaN(mc)) ? null : mc,
      country: d.country || "",
      website: d.website || "",
      etf: (d.sector === "ETF") || (mc === null || isNaN(mc))
    };
  }

  function pickSectorsOrder(stocks) {
    const counts = d3.rollup(stocks, v => v.length, d => d.sector);
    return Array.from(counts.keys()).sort((a, b) => {
      const ca = counts.get(a) || 0;
      const cb = counts.get(b) || 0;
      if (cb !== ca) return cb - ca;
      return a.localeCompare(b);
    });
  }

  function computeClusterCenters(sectors, width, height) {
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.28;
    const centers = {};
    const n = sectors.length;
    if (n === 1) {
      centers[sectors[0]] = [cx, cy];
      return centers;
    }
    if (n === 2) {
      const offset = radius * 0.6;
      centers[sectors[0]] = [cx - offset, cy];
      centers[sectors[1]] = [cx + offset, cy];
      return centers;
    }
    sectors.forEach((sector, i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
      centers[sector] = [
        cx + Math.cos(angle) * radius,
        cy + Math.sin(angle) * radius
      ];
    });
    return centers;
  }

  function buildLegend(sectors) {
    legendEl.innerHTML = "";
    sectors.forEach(sector => {
      const item = document.createElement("div");
      item.className = "legend-item";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.background = SECTOR_COLORS[sector] || "#777";
      const label = document.createElement("span");
      label.textContent = sector;
      item.appendChild(swatch);
      item.appendChild(label);
      legendEl.appendChild(item);
    });
  }

  function buildTable(stocks) {
    tableBody.innerHTML = "";
    stocks.forEach(stock => {
      const mc = formatMarketCap(stock.marketCap);
      const tr = document.createElement("tr");
      tr.dataset.ticker = stock.ticker;

      const tdTicker = document.createElement("td");
      tdTicker.className = "ticker-cell";
      tdTicker.textContent = stock.ticker;

      const tdName = document.createElement("td");
      tdName.className = "name-cell";
      tdName.textContent = stock.fullName;

      const tdSector = document.createElement("td");
      tdSector.className = "sector-cell";
      const pill = document.createElement("span");
      pill.className = "sector-pill";
      pill.style.background = SECTOR_COLORS[stock.sector] || "#777";
      pill.textContent = stock.sector;
      tdSector.appendChild(pill);

      const tdMc = document.createElement("td");
      tdMc.className = "market-cap-cell" + (mc.isNA ? " na" : "");
      tdMc.textContent = mc.text;

      tr.appendChild(tdTicker);
      tr.appendChild(tdName);
      tr.appendChild(tdSector);
      tr.appendChild(tdMc);

      tr.addEventListener("click", () => onTableRowClick(stock.ticker));
      tableBody.appendChild(tr);
    });
  }

  function getSvgSize() {
    const rect = svg.node().getBoundingClientRect();
    state.width = Math.max(360, rect.width);
    state.height = Math.max(320, rect.height);
    svg.attr("viewBox", `0 0 ${state.width} ${state.height}`)
       .attr("preserveAspectRatio", "xMidYMid meet");
  }

  function renderChart(stocks) {
    getSvgSize();

    const sectorList = pickSectorsOrder(stocks);
    state.sectors = sectorList;
    state.sectorCenters = computeClusterCenters(sectorList, state.width, state.height);

    const minRadius = 12;
    const maxRadius = 56;
    const etfRadius = 18;
    const capExtent = d3.extent(stocks, d => d.marketCap).filter(v => v !== null && v !== undefined && isFinite(v));
    const hasCaps = capExtent.length === 2 && isFinite(capExtent[0]) && isFinite(capExtent[1]);
    let radiusScale;
    if (hasCaps) {
      const [lo, hi] = capExtent;
      radiusScale = d3.scaleSqrt()
        .domain([Math.max(lo, 1), hi])
        .range([minRadius, maxRadius]);
    }

    stocks.forEach(d => {
      if (d.etf) {
        d.radius = etfRadius;
      } else if (hasCaps && d.marketCap) {
        d.radius = radiusScale(d.marketCap);
      } else {
        d.radius = etfRadius;
      }
    });

    bubblesGroup.selectAll("*").remove();
    labelsGroup.selectAll("*").remove();

    const bubbles = bubblesGroup.selectAll("circle")
      .data(stocks, d => d.ticker)
      .join("circle")
      .attr("class", d => "bubble" + (d.etf ? " is-etf" : ""))
      .attr("r", d => d.radius)
      .attr("fill", d => SECTOR_COLORS[d.sector] || "#777")
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5);

    const labels = labelsGroup.selectAll("text")
      .data(stocks, d => d.ticker)
      .join("text")
      .attr("class", "bubble-label")
      .text(d => d.ticker);

    bubbles
      .on("mouseover", function (event, d) {
        if (d.etf) return;
        showTooltip(event, d);
      })
      .on("mousemove", function (event, d) {
        if (d.etf) return;
        moveTooltip(event);
      })
      .on("mouseout", function () {
        hideTooltip();
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        selectTicker(d.ticker);
      });

    svg.on("click", () => {
      selectTicker(null);
    });

    if (state.simulation) state.simulation.stop();

    const sim = d3.forceSimulation(stocks)
      .force("x", d3.forceX(d => state.sectorCenters[d.sector][0])
        .strength(0.18))
      .force("y", d3.forceY(d => state.sectorCenters[d.sector][1])
        .strength(0.18))
      .force("collide", d3.forceCollide(d => d.radius + 2).strength(0.9).iterations(2))
      .force("charge", d3.forceManyBody().strength(-6))
      .alpha(1)
      .alphaDecay(0.04);

    sim.on("tick", () => {
      bubbles
        .attr("cx", d => d.x)
        .attr("cy", d => d.y);
      labels
        .attr("x", d => d.x)
        .attr("y", d => d.y);
    });

    state.simulation = sim;
  }

  function showTooltip(event, d) {
    const lines = [];
    lines.push(`<div class="tt-ticker">${escapeHtml(d.ticker)}</div>`);
    lines.push(`<div class="tt-name">${escapeHtml(d.fullName)}</div>`);
    lines.push(`<div class="tt-sector">${escapeHtml(d.sector)}</div>`);
    tooltip.html(lines.join(""));
    tooltip.classed("visible", true).attr("aria-hidden", "false");
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const container = document.getElementById("chart-container");
    const cRect = container.getBoundingClientRect();
    const x = event.clientX - cRect.left;
    const y = event.clientY - cRect.top;
    tooltip.style("left", x + "px").style("top", y + "px");
  }

  function hideTooltip() {
    tooltip.classed("visible", false).attr("aria-hidden", "true");
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function selectTicker(ticker) {
    state.selectedTicker = ticker;
    applySelectionStyles();
    if (ticker) scrollTableToTicker(ticker);
  }

  function applySelectionStyles() {
    const t = state.selectedTicker;
    svg.selectAll("circle.bubble")
      .classed("is-selected", d => d.ticker === t)
      .classed("is-dim", d => t !== null && d.ticker !== t)
      .attr("stroke", d => d.ticker === t ? "#111" : "#ffffff")
      .attr("stroke-width", d => d.ticker === t ? 3 : 1.5);

    svg.selectAll("text.bubble-label")
      .style("font-size", d => d.ticker === t ? "11px" : "10px");

    d3.select(tableBody).selectAll("tr")
      .classed("is-selected", function () {
        return this.dataset.ticker === t;
      });
  }

  function scrollTableToTicker(ticker) {
    const tr = tableBody.querySelector(`tr[data-ticker="${CSS.escape(ticker)}"]`);
    if (tr && tr.scrollIntoView) {
      tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function onTableRowClick(ticker) {
    if (state.selectedTicker === ticker) {
      selectTicker(null);
    } else {
      selectTicker(ticker);
    }
  }

  function handleResize() {
    if (!state.stocks.length) return;
    state.sectorCenters = computeClusterCenters(
      state.sectors, state.width, state.height);
    getSvgSize();
    state.sectorCenters = computeClusterCenters(
      state.sectors, state.width, state.height);
    if (state.simulation) {
      state.simulation
        .force("x", d3.forceX(d => state.sectorCenters[d.sector][0]).strength(0.18))
        .force("y", d3.forceY(d => state.sectorCenters[d.sector][1]).strength(0.18))
        .alpha(0.6)
        .restart();
    }
  }

  function init() {
    d3.csv(CSV_URL).then(raw => {
      const stocks = raw.map(parseRow);
      state.stocks = stocks;
      const sectorList = pickSectorsOrder(stocks);
      buildLegend(sectorList);
      buildTable(stocks);
      renderChart(stocks);
    }).catch(err => {
      console.error("Failed to load CSV", err);
      const c = document.getElementById("chart-container");
      c.textContent = "Failed to load data: " + err.message;
    });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(handleResize, 120);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();