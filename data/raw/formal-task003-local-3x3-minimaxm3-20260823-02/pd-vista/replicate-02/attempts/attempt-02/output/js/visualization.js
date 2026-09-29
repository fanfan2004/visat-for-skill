(function () {
  "use strict";

  const DATA_BASE = "data/";
  const DESC_URL = DATA_BASE + "stock-descriptions.csv";

  const SECTOR_COLORS = d3.scaleOrdinal()
    .domain(["Information Technology", "Industry", "Energy", "Financial", "ETF"])
    .range(["#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#94a3b8"]);

  const ETF_UNIFORM_RADIUS = 16;

  function formatMarketCap(value) {
    if (value === null || value === undefined || value === "" || isNaN(+value)) {
      return "—";
    }
    const n = +value;
    if (n >= 1e12) return (n / 1e12).toFixed(2).replace(/\.00$/, "") + "T";
    if (n >= 1e9) return (n / 1e9).toFixed(2).replace(/\.00$/, "") + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/\.00$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(2).replace(/\.00$/, "") + "K";
    return String(n);
  }

  function parseStockRow(d) {
    const rawCap = d.marketCap;
    let cap = null;
    if (rawCap !== undefined && rawCap !== null && String(rawCap).trim() !== "") {
      const parsed = parseFloat(rawCap);
      if (!isNaN(parsed)) cap = parsed;
    }
    return {
      ticker: d.ticker,
      sector: d.sector,
      name: d["full name"],
      marketCap: cap,
      isETF: d.sector === "ETF",
      country: d.country,
      website: d.website,
    };
  }

  function showError(message) {
    const banner = document.createElement("div");
    banner.className = "error-banner";
    banner.textContent = message;
    document.body.prepend(banner);
    console.error(message);
  }

  function radiusFor(d) {
    if (d.isETF || d.marketCap === null) return ETF_UNIFORM_RADIUS;
    const minR = 10;
    const maxR = 48;
    const minCap = 5e9;
    const maxCap = 3e12;
    const c = Math.max(minCap, Math.min(maxCap, d.marketCap));
    const t = (Math.log(c) - Math.log(minCap)) / (Math.log(maxCap) - Math.log(minCap));
    return minR + t * (maxR - minR);
  }

  function ensureTooltip() {
    let tt = document.querySelector(".tooltip");
    if (!tt) {
      tt = document.createElement("div");
      tt.className = "tooltip";
      document.body.appendChild(tt);
    }
    return tt;
  }

  function positionTooltip(evt, tt) {
    const x = evt.clientX + 14;
    const y = evt.clientY + 14;
    tt.style.left = x + "px";
    tt.style.top = y + "px";
  }

  function renderLegend(svg, sectors, width) {
    const legendG = svg.append("g").attr("class", "legend-group");
    const itemWidth = 150;
    const perRow = Math.max(1, Math.floor(width / itemWidth));
    const items = sectors.map((s) => ({
      sector: s,
      color: SECTOR_COLORS(s),
    }));
    const item = legendG.selectAll("g.legend-item")
      .data(items)
      .enter()
      .append("g")
      .attr("class", "legend-item")
      .attr("transform", (d, i) => {
        const col = i % perRow;
        const row = Math.floor(i / perRow);
        return `translate(${col * itemWidth}, ${row * 20})`;
      });

    item.append("circle")
      .attr("cx", 7)
      .attr("cy", 7)
      .attr("r", 6)
      .attr("fill", (d) => d.color)
      .attr("stroke", "rgba(0,0,0,0.15)");

    item.append("text")
      .attr("x", 18)
      .attr("y", 11)
      .attr("font-size", 11)
      .attr("fill", "#344054")
      .text((d) => d.sector);
  }

  function renderTable(stocks) {
    const tbody = d3.select("#data-table tbody");
    const rows = tbody.selectAll("tr")
      .data(stocks, (d) => d.ticker)
      .enter()
      .append("tr")
      .attr("data-ticker", (d) => d.ticker)
      .on("click", function (event, d) {
        selectStock(d.ticker, { source: "table" });
      });

    rows.append("td").attr("class", "col-ticker").text((d) => d.ticker);
    rows.append("td").attr("class", "col-name").text((d) => d.name);
    rows.append("td").attr("class", "col-sector").html((d) =>
      `<span class="dot" style="background:${SECTOR_COLORS(d.sector)}"></span>${d.sector}`
    );
    rows.append("td").attr("class", "col-cap").text((d) => formatMarketCap(d.marketCap));
  }

  function renderBubbleChart(stocks, chartContainer) {
    const width = chartContainer.clientWidth || 720;
    const height = Math.max(480, Math.round(width * 0.7));

    const svg = d3.select(chartContainer)
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    const sectors = Array.from(new Set(stocks.map((s) => s.sector)));
    const sectorCount = sectors.length;

    const cols = Math.ceil(Math.sqrt(sectorCount));
    const rows = Math.ceil(sectorCount / cols);
    const cellW = width / (cols + 1);
    const cellH = height / (rows + 1);

    const sectorCenters = new Map();
    sectors.forEach((sector, i) => {
      const r = Math.floor(i / cols);
      const c = i % cols;
      sectorCenters.set(sector, {
        x: cellW * (c + 1),
        y: cellH * (r + 1),
      });
    });

    const defs = svg.append("defs");
    const filter = defs.append("filter")
      .attr("id", "bubble-shadow")
      .attr("x", "-30%").attr("y", "-30%")
      .attr("width", "160%").attr("height", "160%");
    filter.append("feGaussianBlur").attr("in", "SourceAlpha").attr("stdDeviation", 2);
    filter.append("feOffset").attr("dx", 0).attr("dy", 1).attr("result", "offsetBlur");
    const merge = filter.append("feMerge");
    merge.append("feMergeNode").attr("in", "offsetBlur");
    merge.append("feMergeNode").attr("in", "SourceGraphic");

    const tt = ensureTooltip();

    const simNodes = stocks.map((s) => Object.assign({}, s, {
      r: radiusFor(s),
      x: sectorCenters.get(s.sector).x + (Math.random() - 0.5) * 30,
      y: sectorCenters.get(s.sector).y + (Math.random() - 0.5) * 30,
    }));

    const simulation = d3.forceSimulation(simNodes)
      .force("x", d3.forceX((d) => sectorCenters.get(d.sector).x).strength(0.18))
      .force("y", d3.forceY((d) => sectorCenters.get(d.sector).y).strength(0.18))
      .force("collide", d3.forceCollide((d) => d.r + 2).strength(0.9))
      .force("charge", d3.forceManyBody().strength(-6))
      .stop();

    for (let i = 0; i < 320; i += 1) simulation.tick();

    const bg = svg.append("rect")
      .attr("class", "chart-bg")
      .attr("width", width)
      .attr("height", height)
      .attr("fill", "transparent")
      .on("click", () => clearSelection());

    const g = svg.append("g").attr("class", "bubbles");

    const node = g.selectAll("g.bubble")
      .data(simNodes, (d) => d.ticker)
      .enter()
      .append("g")
      .attr("class", "bubble")
      .attr("data-ticker", (d) => d.ticker)
      .attr("transform", (d) => `translate(${d.x}, ${d.y})`);

    node.append("circle")
      .attr("r", (d) => d.r)
      .attr("fill", (d) => SECTOR_COLORS(d.sector))
      .attr("fill-opacity", 0.78)
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5)
      .attr("filter", "url(#bubble-shadow)");

    node.append("text")
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-size", (d) => Math.max(8, Math.min(13, d.r * 0.55)))
      .attr("font-weight", 700)
      .attr("fill", "#ffffff")
      .attr("paint-order", "stroke")
      .attr("stroke", "rgba(0,0,0,0.35)")
      .attr("stroke-width", 0.5)
      .text((d) => d.ticker);

    node.style("cursor", (d) => d.isETF ? "default" : "pointer")
      .on("mouseover", function (event, d) {
        if (d.isETF) {
          tt.style.opacity = 0;
          return;
        }
        const html =
          `<div class="tt-ticker">${d.ticker}</div>` +
          `<div class="tt-name">${escapeHtml(d.name)}</div>` +
          `<div class="tt-sector">Sector: ${escapeHtml(d.sector)}</div>`;
        tt.innerHTML = html;
        tt.style.opacity = 1;
        positionTooltip(event, tt);
        d3.select(this).select("circle").attr("stroke", "#0f172a").attr("stroke-width", 2.5);
      })
      .on("mousemove", function (event) {
        if (tt.style.opacity === "1") positionTooltip(event, tt);
      })
      .on("mouseout", function () {
        tt.style.opacity = 0;
        d3.select(this).select("circle").attr("stroke", "#ffffff").attr("stroke-width", 1.5);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        selectStock(d.ticker, { source: "bubble" });
      });

    renderLegend(svg, sectors, width);

    return {
      svg,
      simulation,
      nodes: simNodes,
    };
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

  function selectStock(ticker, opts) {
    opts = opts || {};
    d3.selectAll("#data-table tbody tr").classed("selected", function () {
      return d3.select(this).attr("data-ticker") === ticker;
    });
    d3.selectAll("g.bubble").classed("selected", function () {
      return d3.select(this).attr("data-ticker") === ticker;
    });

    if (opts.source === "bubble") {
      const row = document.querySelector(`#data-table tbody tr[data-ticker="${ticker}"]`);
      if (row) row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else if (opts.source === "table") {
      const bubble = document.querySelector(`g.bubble[data-ticker="${ticker}"]`);
      if (bubble && typeof bubble.scrollIntoView === "function") {
        bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }

    d3.selectAll("g.bubble.selected circle")
      .attr("stroke", "#0f172a")
      .attr("stroke-width", 3);
    d3.selectAll("g.bubble:not(.selected) circle")
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5);
  }

  function clearSelection() {
    d3.selectAll("#data-table tbody tr.selected").classed("selected", false);
    d3.selectAll("g.bubble.selected").classed("selected", false);
    d3.selectAll("g.bubble circle")
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const chartEl = document.getElementById("chart");

    d3.csv(DESC_URL).then((rawRows) => {
      const stocks = rawRows.map(parseStockRow).filter((d) => d.ticker);
      stocks.sort((a, b) => a.ticker.localeCompare(b.ticker));
      renderTable(stocks);
      renderBubbleChart(stocks, chartEl);
    }).catch((err) => {
      showError("Failed to load stock descriptions: " + err.message);
    });
  });
})();
