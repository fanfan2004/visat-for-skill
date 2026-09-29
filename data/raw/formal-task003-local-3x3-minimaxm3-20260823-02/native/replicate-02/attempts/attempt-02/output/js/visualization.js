/**
 * Stock Cluster Visualization
 *
 * Side-by-side bubble chart + interactive data table.
 * - Bubble chart uses a deterministic D3.js v6 force simulation that
 *   clusters stocks by sector using forceX/forceY, sizes by market cap,
 *   uses forceCollide to prevent overlap, and labels each bubble with
 *   its ticker symbol.
 * - Tooltip on hover shows ticker / full name / sector (omitted for ETFs
 *   which lack marketCap / country / website).
 * - Data table shows all 50 stocks with formatted market cap.
 * - Two-way linking: clicking a bubble highlights its table row and
 *   vice versa.
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------
  // Determinism helpers
  // ---------------------------------------------------------------
  const SIM_TICKS = 300;            // fixed simulation iterations
  const SEED = 1337;                // deterministic RNG seed
  const STABLE_BAND_TICKS = 20;     // extra ticks to settle after forceX/Y

  // Mulberry32 PRNG – used only for stable initialization, not for layout.
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---------------------------------------------------------------
  // Configuration
  // ---------------------------------------------------------------
  const chartEl = document.getElementById("chart");
  const tooltipEl = document.getElementById("tooltip");
  const legendEl = document.getElementById("legend");
  const tableEl = document.getElementById("data-table");
  const tableBodyEl = tableEl.querySelector("tbody");

  const SECTOR_ORDER = [
    "Information Technology",
    "Industry",
    "Energy",
    "Financial",
    "ETF",
  ];
  const SECTOR_COLORS = {
    "Information Technology": "#4f46e5", // indigo
    "Industry": "#0ea5e9",              // sky
    "Energy": "#f97316",                // orange
    "Financial": "#10b981",             // emerald
    "ETF": "#9ca3af",                   // neutral gray
  };

  // ---------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------
  function formatMarketCap(value) {
    if (value === null || value === undefined || value === "" || isNaN(value)) {
      return null;
    }
    const abs = Math.abs(value);
    let formatted;
    if (abs >= 1e12) formatted = (value / 1e12).toFixed(2) + "T";
    else if (abs >= 1e9) formatted = (value / 1e9).toFixed(2) + "B";
    else if (abs >= 1e6) formatted = (value / 1e6).toFixed(2) + "M";
    else if (abs >= 1e3) formatted = (value / 1e3).toFixed(2) + "K";
    else formatted = value.toFixed(2);
    return value < 0 ? "-" + formatted : formatted;
  }

  function tickerRowKey(ticker) {
    return "row-" + ticker;
  }

  // ---------------------------------------------------------------
  // State
  // ---------------------------------------------------------------
  let stocks = [];          // raw rows, sorted deterministically
  let bubbles = [];         // d3-bound nodes
  let selectedTicker = null;
  let sortKey = "ticker";
  let sortDir = "asc";

  // ---------------------------------------------------------------
  // Bubble chart
  // ---------------------------------------------------------------
  function renderChart() {
    chartEl.innerHTML = "";

    // Decide a size that scales with the panel
    const rect = chartEl.getBoundingClientRect();
    const width = Math.max(520, Math.floor(rect.width || 760));
    const height = 560;
    const radius = Math.min(width, height) / 2 - 30;

    const svg = d3
      .select(chartEl)
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet")
      .attr("role", "img")
      .attr("aria-label", "Stock bubbles clustered by sector");

    const chartGroup = svg
      .append("g")
      .attr("transform", `translate(${width / 2},${height / 2})`);

    // Radius scale (sqrt) using non-ETF caps
    const capValues = stocks
      .map((d) => d.marketCap)
      .filter((v) => typeof v === "number" && !isNaN(v));
    const capExtent = d3.extent(capValues);
    const minR = 14;
    const maxR = 56;
    const rScale = d3
      .scaleSqrt()
      .domain(capExtent[0] !== undefined ? capExtent : [0, 1])
      .range([minR, maxR]);
    const ETF_RADIUS = 18;

    // Pre-compute per-node radius and sector anchor
    const rng = mulberry32(SEED);
    stocks.forEach((d) => {
      d.radius = d.isETF ? ETF_RADIUS : Math.max(minR, rScale(d.marketCap));
      // Deterministic polar offset inside the sector cluster
      d.theta = rng() * Math.PI * 2;
      d.rOffset = rng() * 0.6; // 0..0.6 of cluster radius
    });

    // Place sector anchors on a tight ring (so clusters stay close together)
    const sectorsPresent = SECTOR_ORDER.filter((s) =>
      stocks.some((d) => d.sector === s)
    );
    const nSectors = sectorsPresent.length;
    const anchorRingRadius = Math.min(width, height) / 2 - 130;
    const sectorCenters = {};
    sectorsPresent.forEach((sector, i) => {
      const angle = (i / nSectors) * Math.PI * 2 - Math.PI / 2;
      sectorCenters[sector] = {
        x: Math.cos(angle) * anchorRingRadius,
        y: Math.sin(angle) * anchorRingRadius,
      };
    });

    // Force simulation
    const simulation = d3
      .forceSimulation(stocks)
      .force(
        "collide",
        d3.forceCollide().radius((d) => d.radius + 2).iterations(2)
      )
      .force("charge", d3.forceManyBody().strength(-25))
      .force(
        "x",
        d3
          .forceX((d) => sectorCenters[d.sector].x)
          .strength(0.25)
      )
      .force(
        "y",
        d3
          .forceY((d) => sectorCenters[d.sector].y)
          .strength(0.25)
      )
      .stop();

    // Run a fixed number of deterministic ticks
    for (let i = 0; i < SIM_TICKS + STABLE_BAND_TICKS; i++) simulation.tick();

    // Clamp bubble centers inside the chart's visible circle so labels
    // are not clipped by the SVG edge.
    const clampR = Math.min(width, height) / 2 - 20;
    stocks.forEach((d) => {
      const r = Math.hypot(d.x, d.y);
      const limit = clampR - d.radius - 2;
      if (r > limit && r > 0) {
        const k = limit / r;
        d.x *= k;
        d.y *= k;
      }
    });

    // Draw bubbles
    const nodeGroup = chartGroup
      .selectAll("g.node")
      .data(stocks, (d) => d.ticker)
      .join("g")
      .attr("class", "node")
      .attr("data-ticker", (d) => d.ticker)
      .attr("transform", (d) => `translate(${d.x},${d.y})`);

    nodeGroup
      .append("circle")
      .attr("class", "bubble")
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => SECTOR_COLORS[d.sector] || "#888");

    nodeGroup
      .append("text")
      .attr("class", "bubble-label")
      .attr("font-size", (d) => Math.max(9, Math.min(14, d.radius / 2.6)))
      .text((d) => d.ticker);

    bubbles = nodeGroup;

    // Interactions
    nodeGroup
      .on("mouseover", function (event, d) {
        if (d.isETF) return;
        showTooltip(event, d);
      })
      .on("mousemove", function (event, d) {
        if (d.isETF) return;
        positionTooltip(event);
      })
      .on("mouseout", function () {
        hideTooltip();
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        setSelected(d.ticker);
      });

    // Background click clears selection
    svg.on("click", () => setSelected(null));

    // Legend
    renderLegend();

    // Apply any pre-existing selection
    applySelectionStyles();
  }

  function renderLegend() {
    legendEl.innerHTML = "";
    const sectorsPresent = SECTOR_ORDER.filter((s) =>
      stocks.some((d) => d.sector === s)
    );
    sectorsPresent.forEach((sector) => {
      const item = document.createElement("div");
      item.className = "legend-item";

      const sw = document.createElement("span");
      sw.className = "legend-swatch";
      sw.style.background = SECTOR_COLORS[sector] || "#888";

      const label = document.createElement("span");
      label.textContent = sector;

      item.appendChild(sw);
      item.appendChild(label);
      legendEl.appendChild(item);
    });
  }

  // ---------------------------------------------------------------
  // Tooltip
  // ---------------------------------------------------------------
  function showTooltip(event, d) {
    const html =
      `<strong>${d.ticker}</strong>` +
      `<div>${d.fullName || ""}</div>` +
      `<div>Sector: ${d.sector}</div>`;
    tooltipEl.innerHTML = html;
    tooltipEl.classList.add("visible");
    positionTooltip(event);
  }

  function positionTooltip(event) {
    const panelRect = chartEl.getBoundingClientRect();
    const x = event.clientX - panelRect.left;
    const y = event.clientY - panelRect.top;
    tooltipEl.style.left = x + "px";
    tooltipEl.style.top = y + "px";
  }

  function hideTooltip() {
    tooltipEl.classList.remove("visible");
  }

  // ---------------------------------------------------------------
  // Data table
  // ---------------------------------------------------------------
  function renderTable() {
    tableBodyEl.innerHTML = "";

    const sorted = stocks.slice().sort(compareStocks);

    sorted.forEach((d) => {
      const tr = document.createElement("tr");
      tr.setAttribute("id", tickerRowKey(d.ticker));
      tr.dataset.ticker = d.ticker;

      const tdTicker = document.createElement("td");
      tdTicker.textContent = d.ticker;
      tr.appendChild(tdTicker);

      const tdName = document.createElement("td");
      tdName.textContent = d.fullName || "";
      tr.appendChild(tdName);

      const tdSector = document.createElement("td");
      tdSector.textContent = d.sector || "";
      tr.appendChild(tdSector);

      const tdCap = document.createElement("td");
      tdCap.classList.add("num");
      const capText = formatMarketCap(d.marketCap);
      if (capText === null) {
        tdCap.innerHTML = '<span class="empty-cell">—</span>';
      } else {
        tdCap.textContent = capText;
      }
      tr.appendChild(tdCap);

      tr.addEventListener("click", () => setSelected(d.ticker));
      tableBodyEl.appendChild(tr);
    });

    updateSortIndicators();
    applySelectionStyles();
  }

  function compareStocks(a, b) {
    const dir = sortDir === "asc" ? 1 : -1;
    const ka = a[sortKey];
    const kb = b[sortKey];
    if (sortKey === "marketCap") {
      const va = typeof ka === "number" ? ka : -Infinity;
      const vb = typeof kb === "number" ? kb : -Infinity;
      return (va - vb) * dir;
    }
    const sa = ka === null || ka === undefined ? "" : String(ka);
    const sb = kb === null || kb === undefined ? "" : String(kb);
    return sa.localeCompare(sb) * dir;
  }

  function updateSortIndicators() {
    tableEl.querySelectorAll("thead th").forEach((th) => {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.dataset.key === sortKey) {
        th.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  // ---------------------------------------------------------------
  // Selection linking
  // ---------------------------------------------------------------
  function setSelected(ticker) {
    selectedTicker = ticker;
    applySelectionStyles();
    if (ticker) {
      const row = document.getElementById(tickerRowKey(ticker));
      if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function applySelectionStyles() {
    // Bubble styles
    bubbles.each(function (d) {
      const sel = selectedTicker && d.ticker === selectedTicker;
      d3.select(this)
        .select("circle.bubble")
        .classed("selected", !!sel)
        .classed("dimmed", selectedTicker && !sel);
    });

    // Row styles
    tableBodyEl.querySelectorAll("tr").forEach((tr) => {
      tr.classList.toggle("selected", tr.dataset.ticker === selectedTicker);
    });
  }

  // ---------------------------------------------------------------
  // Sort header binding
  // ---------------------------------------------------------------
  function bindSortHeaders() {
    tableEl.querySelectorAll("thead th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (sortKey === key) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortKey = key;
          sortDir = "asc";
        }
        renderTable();
      });
    });
  }

  // ---------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------
  function parseRow(row) {
    const ticker = (row.ticker || "").trim().toUpperCase();
    const sector = (row.sector || "").trim();
    const fullName = (row["full name"] || "").trim();
    const rawCap = (row.marketCap || "").trim();
    const marketCap = rawCap === "" ? null : Number(rawCap);
    return {
      ticker,
      sector,
      fullName,
      marketCap,
      isETF: sector === "ETF",
    };
  }

  d3.csv("data/stock-descriptions.csv")
    .then((rows) => {
      stocks = rows
        .map(parseRow)
        .filter((d) => d.ticker)
        .sort((a, b) => a.ticker.localeCompare(b.ticker));

      bindSortHeaders();
      renderChart();
      renderTable();

      window.addEventListener("resize", () => {
        // Re-run simulation deterministically on resize
        renderChart();
      });
    })
    .catch((err) => {
      console.error("Failed to load stock-descriptions.csv", err);
      chartEl.textContent = "Failed to load data.";
    });
})();