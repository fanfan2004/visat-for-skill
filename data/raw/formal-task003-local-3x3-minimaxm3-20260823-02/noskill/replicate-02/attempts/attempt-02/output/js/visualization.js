(function () {
  "use strict";

  const DATA_URL = "data/stock-descriptions.csv";
  const CHART_ID = "bubble-chart";
  const TOOLTIP_CLASS = "tooltip";
  const LEGEND_ID = "sector-legend";
  const TABLE_ID = "stocks-table";
  const TABLE_BODY_ID = "stocks-table-body";

  const SECTOR_ORDER = [
    "Information Technology",
    "Financial",
    "Energy",
    "Industry",
    "ETF",
  ];

  const SECTOR_COLORS = {
    "Information Technology": "#1f77b4",
    Financial: "#2ca02c",
    Energy: "#d62728",
    Industry: "#ff7f0e",
    ETF: "#7f7f7f",
  };

  function formatMarketCap(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    if (Math.abs(value) >= 1e12) return (value / 1e12).toFixed(2) + "T";
    if (Math.abs(value) >= 1e9) return (value / 1e9).toFixed(2) + "B";
    if (Math.abs(value) >= 1e6) return (value / 1e6).toFixed(2) + "M";
    if (Math.abs(value) >= 1e3) return (value / 1e3).toFixed(2) + "K";
    return String(value);
  }

  function parseMarketCap(raw) {
    if (raw === null || raw === undefined || raw === "") return null;
    const n = +raw;
    return Number.isFinite(n) ? n : null;
  }

  function selectTicker(state, ticker) {
    state.selectedTicker = state.selectedTicker === ticker ? null : ticker;
    renderSelection(state);
  }

  function renderSelection(state) {
    const sel = state.selectedTicker;
    state.bubbleGroup
      .selectAll("circle.bubble")
      .classed("active", (d) => d.ticker === sel)
      .classed("dim", (d) => sel !== null && d.ticker !== sel);

    state.bubbleGroup
      .selectAll("text.bubble-label")
      .style("opacity", (d) =>
        sel === null ? 1 : d.ticker === sel ? 1 : 0.35
      );

    state.tableBody
      .selectAll("tr.stock-row")
      .classed("selected", (d) => d.ticker === sel);

    if (sel) {
      const node = state.tableBody
        .selectAll("tr.stock-row")
        .filter((d) => d.ticker === sel)
        .node();
      if (node && typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  function buildLegend(svg, sectors, colorScale) {
    const legend = d3.select("#" + LEGEND_ID);
    legend.selectAll("*").remove();
    legend
      .selectAll("span.legend-item")
      .data(sectors)
      .join("span")
      .attr("class", "legend-item")
      .html((d) => {
        const c = colorScale(d);
        return `<span class="legend-swatch" style="background:${c}"></span><span>${d}</span>`;
      });
  }

  function getContainerSize() {
    const node = document.getElementById("chart-section");
    if (!node) return { width: 800, height: 620 };
    const width = node.clientWidth || 800;
    return { width, height: 620 };
  }

  function computeSectorCenters(sectors, width, height) {
    const padX = Math.min(180, width / 5);
    const padY = Math.min(160, height / 5);
    const innerW = width - 2 * padX;
    const innerH = height - 2 * padY;
    const center = (sectors.length - 1) || 0;

    return sectors.map((_, i) => {
      const angle = (i / sectors.length) * 2 * Math.PI;
      const rx = innerW / 2;
      const ry = innerH / 2;
      return {
        x: padX + innerW / 2 + rx * Math.cos(angle),
        y: padY + innerH / 2 + ry * Math.sin(angle),
      };
    });
  }

  function buildChart(stocks, state) {
    const { width, height } = getContainerSize();

    const sectorsPresent = Array.from(new Set(stocks.map((d) => d.sector)));
    sectorsPresent.sort((a, b) => {
      const ia = SECTOR_ORDER.indexOf(a);
      const ib = SECTOR_ORDER.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });

    const colorScale = d3
      .scaleOrdinal()
      .domain(sectorsPresent)
      .range(
        sectorsPresent.map((s) => SECTOR_COLORS[s] || "#999999")
      );

    state.colorScale = colorScale;
    state.sectors = sectorsPresent;

    const centers = computeSectorCenters(sectorsPresent, width, height);
    const centerMap = new Map(
      sectorsPresent.map((s, i) => [s, centers[i]])
    );

    const maxCap = d3.max(stocks, (d) => d.marketCap || 0) || 1;
    const minR = 18;
    const maxR = 64;
    const radiusScale = d3
      .scaleSqrt()
      .domain([0, maxCap])
      .range([minR, maxR]);

    const etfRadius = minR + (maxR - minR) * 0.32;

    stocks.forEach((d) => {
      d.radius = d.isETF ? etfRadius : radiusScale(d.marketCap || 0);
      const c = centerMap.get(d.sector);
      d.targetX = c ? c.x : width / 2;
      d.targetY = c ? c.y : height / 2;
    });

    const svg = d3
      .select("#" + CHART_ID)
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    svg.selectAll("*").remove();

    const defs = svg.append("defs");

    const filter = defs
      .append("filter")
      .attr("id", "bubble-shadow")
      .attr("x", "-30%")
      .attr("y", "-30%")
      .attr("width", "160%")
      .attr("height", "160%");
    filter
      .append("feGaussianBlur")
      .attr("in", "SourceAlpha")
      .attr("stdDeviation", 2);
    filter
      .append("feOffset")
      .attr("dx", 0)
      .attr("dy", 1)
      .attr("result", "offsetblur");
    filter
      .append("feComponentTransfer")
      .append("feFuncA")
      .attr("type", "linear")
      .attr("slope", 0.25);
    const feMerge = filter.append("feMerge");
    feMerge.append("feMergeNode");
    feMerge.append("feMergeNode").attr("in", "SourceGraphic");

    const bubbleGroup = svg.append("g").attr("class", "bubbles");
    state.bubbleGroup = bubbleGroup;

    const tooltip = d3
      .select("#chart-section")
      .append("div")
      .attr("class", TOOLTIP_CLASS);

    const showTooltip = (event, d) => {
      if (d.isETF) return;
      tooltip
        .style("opacity", 1)
        .html(
          `<div class="tooltip-ticker">${d.ticker}</div>` +
            `<div><span class="tooltip-label">Name:</span> ${d.name}</div>` +
            `<div><span class="tooltip-label">Sector:</span> ${d.sector}</div>`
        );
      moveTooltip(event);
    };

    const moveTooltip = (event) => {
      const containerRect = document
        .getElementById("chart-section")
        .getBoundingClientRect();
      const x = event.clientX - containerRect.left + 14;
      const y = event.clientY - containerRect.top + 14;
      tooltip.style("left", x + "px").style("top", y + "px");
    };

    const hideTooltip = () => {
      tooltip.style("opacity", 0);
    };

    const nodes = stocks.map((d) => Object.assign({}, d));

    const simulation = d3
      .forceSimulation(nodes)
      .force(
        "x",
        d3
          .forceX((d) => d.targetX)
          .strength(0.18)
      )
      .force(
        "y",
        d3
          .forceY((d) => d.targetY)
          .strength(0.18)
      )
      .force(
        "collide",
        d3.forceCollide((d) => d.radius + 1.5).strength(0.95)
      )
      .force(
        "charge",
        d3.forceManyBody().strength((d) => -Math.pow(d.radius, 1.4) * 0.6)
      )
      .alpha(1)
      .alphaDecay(0.035)
      .stop();

    for (let i = 0; i < 220; i++) simulation.tick();

    const bubble = bubbleGroup
      .selectAll("circle.bubble")
      .data(nodes, (d) => d.ticker)
      .join("circle")
      .attr("class", "bubble")
      .attr("r", (d) => d.radius)
      .attr("cx", (d) => d.x)
      .attr("cy", (d) => d.y)
      .attr("fill", (d) => colorScale(d.sector))
      .attr("filter", "url(#bubble-shadow)")
      .on("mouseenter", function (event, d) {
        d3.select(this).raise();
        showTooltip(event, d);
      })
      .on("mousemove", moveTooltip)
      .on("mouseleave", hideTooltip)
      .on("click", function (event, d) {
        event.stopPropagation();
        selectTicker(state, d.ticker);
      });

    bubbleGroup
      .selectAll("text.bubble-label")
      .data(nodes, (d) => d.ticker)
      .join("text")
      .attr("class", "bubble-label")
      .attr("x", (d) => d.x)
      .attr("y", (d) => d.y)
      .attr("font-size", (d) => Math.max(10, Math.min(d.radius / 3.2, 14)))
      .text((d) => d.ticker);

    bubbleGroup
      .selectAll("circle.bubble")
      .on("mouseenter", function (event, d) {
        d3.select(this).raise();
        showTooltip(event, d);
        bubbleGroup
          .selectAll("text.bubble-label")
          .filter((n) => n.ticker === d.ticker)
          .raise();
      });

    svg.on("click", () => {
      if (state.selectedTicker !== null) {
        state.selectedTicker = null;
        renderSelection(state);
      }
    });

    buildLegend(svg, sectorsPresent, colorScale);

    state.bubbleSim = simulation;
    state.bubbles = nodes;
  }

  function buildTable(stocks, state) {
    const tbody = d3.select("#" + TABLE_BODY_ID);
    tbody.selectAll("*").remove();

    const rows = tbody
      .selectAll("tr.stock-row")
      .data(stocks, (d) => d.ticker)
      .join("tr")
      .attr("class", "stock-row")
      .attr("data-ticker", (d) => d.ticker)
      .on("click", function (event, d) {
        event.stopPropagation();
        selectTicker(state, d.ticker);
      });

    rows.append("td").attr("class", "ticker-cell").text((d) => d.ticker);

    rows.append("td").text((d) => d.name);

    rows
      .append("td")
      .attr("class", "sector-cell")
      .html((d) => {
        const c = state.colorScale(d.sector);
        return `<span class="legend-swatch" style="background:${c}"></span><span>${d.sector}</span>`;
      });

    rows
      .append("td")
      .attr("class", "market-cap-cell")
      .text((d) =>
        d.isETF ? "—" : formatMarketCap(d.marketCap)
      );

    state.tableBody = tbody;
    state.tableRows = rows;
  }

  function main() {
    const state = {
      selectedTicker: null,
      bubbleGroup: null,
      tableBody: null,
      colorScale: null,
      sectors: null,
    };

    d3.csv(DATA_URL).then((raw) => {
      const stocks = raw
        .map((d) => {
          const cap = parseMarketCap(d.marketCap);
          const isETF = !cap || d.sector === "ETF";
          return {
            ticker: (d.ticker || "").trim(),
            sector: (d.sector || "Unknown").trim(),
            name: (d["full name"] || d.ticker || "").trim(),
            marketCap: cap,
            isETF,
          };
        })
        .filter((d) => d.ticker)
        .sort((a, b) => a.ticker.localeCompare(b.ticker));

      buildChart(stocks, state);
      buildTable(stocks, state);
      window.addEventListener("resize", () => {
        buildChart(stocks, state);
        buildTable(stocks, state);
        renderSelection(state);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
