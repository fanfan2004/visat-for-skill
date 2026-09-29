/* Stock Market Visualization
 * D3.js v6 force-directed bubble chart + linked data table
 */
(function () {
  "use strict";

  const DATA_URL = "data/stock-descriptions.csv";

  // ---------- chart sizing ----------
  const CHART_WIDTH = 720;
  const CHART_HEIGHT = 640;
  const MARGIN = { top: 50, right: 40, bottom: 50, left: 40 };

  // ---------- bubble sizing ----------
  const ETF_UNIFORM_RADIUS = 16; // ETFs have no market cap; size them uniformly
  const MIN_RADIUS = 14;
  const MAX_RADIUS = 48;
  const COLLIDE_PADDING = 3;

  // ---------- sector palette ----------
  // d3.schemeCategory10-like palette, but ensure good contrast on white.
  const SECTOR_COLORS = {
    "Information Technology": "#2563eb",
    "Industry": "#ea580c",
    "Energy": "#ca8a04",
    "Financial": "#16a34a",
    "ETF": "#9333ea"
  };

  const SECTOR_ORDER = [
    "Information Technology",
    "Industry",
    "Energy",
    "Financial",
    "ETF"
  ];

  // Cluster centers placed inside the chart inner area in a tidy arrangement
  // (3 on the top row, 2 on the bottom row; balanced spacing).
  const innerLeft = MARGIN.left;
  const innerRight = CHART_WIDTH - MARGIN.right;
  const innerTop = MARGIN.top;
  const innerBottom = CHART_HEIGHT - MARGIN.bottom;
  const innerCx = (innerLeft + innerRight) / 2;
  const innerCy = (innerTop + innerBottom) / 2;

  function sectorClusters() {
    const colSplit = (innerLeft + innerCx) / 2;
    const colMid = innerCx;
    const colRight = (innerCx + innerRight) / 2;
    const rowTop = (innerTop + innerCy) / 2 + 30; // lower so header has room
    const rowBottom = (innerCy + innerBottom) / 2 + 60;
    return {
      "Information Technology": { x: colSplit, y: rowTop, labelDx: 0, labelDy: -180 },
      "Industry": { x: colMid, y: rowTop, labelDx: 0, labelDy: -180 },
      "Energy": { x: colRight, y: rowTop, labelDx: 0, labelDy: -180 },
      "Financial": { x: colSplit, y: rowBottom, labelDx: 0, labelDy: -180 },
      "ETF": { x: colRight, y: rowBottom, labelDx: 0, labelDy: -180 }
    };
  }

  const CLUSTER_CENTERS = sectorClusters();

  // ---------- helpers ----------
  function isETF(d) {
    return d.sector === "ETF";
  }

  function formatMarketCap(value) {
    if (value == null || isNaN(value)) return "—";
    const abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(2).replace(/\.00$/, "") + "T";
    if (abs >= 1e9) return (value / 1e9).toFixed(2).replace(/\.00$/, "") + "B";
    if (abs >= 1e6) return (value / 1e6).toFixed(2).replace(/\.00$/, "") + "M";
    if (abs >= 1e3) return (value / 1e3).toFixed(2).replace(/\.00$/, "") + "K";
    return String(value);
  }

  // ---------- shared state ----------
  const state = {
    raw: [],
    selectedTicker: null
  };

  // ---------- tooltip ----------
  const tooltip = (function () {
    const el = document.getElementById("tooltip");
    return {
      show(html, evt) {
        el.innerHTML = html;
        el.classList.add("visible");
        el.setAttribute("aria-hidden", "false");
        this.move(evt);
      },
      move(evt) {
        const pad = 14;
        const rect = el.getBoundingClientRect();
        let x = evt.clientX + pad;
        let y = evt.clientY + pad;
        if (x + rect.width > window.innerWidth) x = evt.clientX - rect.width - pad;
        if (y + rect.height > window.innerHeight) y = evt.clientY - rect.height - pad;
        el.style.left = x + "px";
        el.style.top = y + "px";
      },
      hide() {
        el.classList.remove("visible");
        el.setAttribute("aria-hidden", "true");
      }
    };
  })();

  // ---------- main ----------
  document.addEventListener("DOMContentLoaded", main);

  function main() {
    d3.csv(DATA_URL).then(onDataLoaded).catch(function (err) {
      console.error("Failed to load", DATA_URL, err);
      const container = document.getElementById("chart-container");
      container.innerHTML =
        "<p style='color:#b91c1c;padding:16px;'>Failed to load stock-descriptions.csv. " +
        "Make sure the data folder is alongside index.html.</p>";
    });
  }

  function onDataLoaded(rows) {
    // parse and clean
    const stocks = rows.map(function (r) {
      const mc = r.marketCap && r.marketCap !== "" ? +r.marketCap : null;
      return {
        ticker: r.ticker,
        name: r["full name"],
        sector: r.sector,
        marketCap: mc,
        country: r.country,
        website: r.website
      };
    });

    state.raw = stocks;

    // build radius scale based on the stocks that actually have a market cap
    const withCap = stocks.filter(function (d) { return !isETF(d) && d.marketCap != null; });
    const capExtent = d3.extent(withCap, function (d) { return d.marketCap; });

    function radiusFor(d) {
      if (isETF(d)) return ETF_UNIFORM_RADIUS;
      if (d.marketCap == null) return ETF_UNIFORM_RADIUS;
      const scale = d3
        .scaleSqrt()
        .domain([capExtent[0], capExtent[1]])
        .range([MIN_RADIUS, MAX_RADIUS]);
      return scale(d.marketCap);
    }

    // reverse lookup for radius inside the simulation
    stocks.forEach(function (d) { d.radius = radiusFor(d); });

    renderBubbleChart(stocks);
    renderTable(stocks);

    // auto-select first ticker so the table/bubble aren't visually "empty"
    if (stocks.length) selectTicker(stocks[0].ticker);
  }

  // ---------- bubble chart ----------
  function renderBubbleChart(stocks) {
    const container = document.getElementById("chart-container");
    container.innerHTML = "";

    const width = CHART_WIDTH;
    const height = CHART_HEIGHT;
    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;

    const svg = d3
      .select(container)
      .append("svg")
      .attr("id", "bubble-chart")
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("preserveAspectRatio", "xMidYMid meet")
      .attr("width", "100%")
      .attr("height", height);

    const root = svg
      .append("g")
      .attr("transform", "translate(" + MARGIN.left + "," + MARGIN.top + ")");

    // background panel
    root
      .append("rect")
      .attr("class", "chart-bg")
      .attr("x", -MARGIN.left)
      .attr("y", -MARGIN.top)
      .attr("width", width)
      .attr("height", height)
      .attr("fill", "#ffffff");

    // cluster labels (drawn before simulation, will be repositioned after)
    const labelGroup = root.append("g").attr("class", "cluster-labels");
    const clusterLabels = labelGroup
      .selectAll("text.cluster-label")
      .data(SECTOR_ORDER)
      .join("text")
      .attr("class", "cluster-label")
      .attr("text-anchor", "middle")
      .attr("x", function (s) { return CLUSTER_CENTERS[s].x - MARGIN.left; })
      .attr("y", function (s) {
        // Place label slightly above the cluster center in the inner box.
        // For top-row clusters, push the label just above their bubbles.
        // For bottom-row clusters, the label sits closer to the cluster itself.
        const c = CLUSTER_CENTERS[s];
        const localY = c.y - MARGIN.top - 175;
        return Math.max(15, localY);
      })
      .text(function (s) { return s; });

    const simulation = d3
      .forceSimulation(stocks)
      .alphaDecay(0.04)
      .velocityDecay(0.4)
      .force(
        "x",
        d3
          .forceX()
          .strength(0.18)
          .x(function (d) { return CLUSTER_CENTERS[d.sector].x - MARGIN.left; })
      )
      .force(
        "y",
        d3
          .forceY()
          .strength(0.18)
          .y(function (d) { return CLUSTER_CENTERS[d.sector].y - MARGIN.top; })
      )
      .force(
        "collide",
        d3.forceCollide().radius(function (d) { return d.radius + COLLIDE_PADDING; }).iterations(2)
      );

    const bubblesGroup = root
      .append("g")
      .attr("class", "bubbles");

    const bubbles = bubblesGroup
      .selectAll("g.bubble")
      .data(stocks, function (d) { return d.ticker; })
      .join("g")
      .attr("class", function (d) { return "bubble-group" + (isETF(d) ? " etf-group" : ""); })
      .attr("data-ticker", function (d) { return d.ticker; });

    bubbles
      .append("circle")
      .attr("class", function (d) { return "bubble" + (isETF(d) ? " is-etf" : ""); })
      .attr("r", function (d) { return d.radius; })
      .attr("fill", function (d) { return SECTOR_COLORS[d.sector] || "#6b7280"; })
      .attr("opacity", 0.92)
      .on("mouseover", function (evt, d) {
        if (isETF(d)) return; // per spec: no tooltip for ETFs
        tooltip.show(
          "<div class='tooltip-ticker'>" + d.ticker + "</div>" +
            "<div class='tooltip-row'>" +
            "<span class='label'>Name: </span>" + escapeHTML(d.name) +
            "</div>" +
            "<div class='tooltip-row'>" +
            "<span class='label'>Sector: </span>" + escapeHTML(d.sector) +
            "</div>",
          evt
        );
      })
      .on("mousemove", function (evt, d) {
        if (isETF(d)) return;
        tooltip.move(evt);
      })
      .on("mouseout", function () { tooltip.hide(); })
      .on("click", function (evt, d) {
        evt.stopPropagation();
        selectTicker(d.ticker);
      });

    bubbles
      .append("text")
      .attr("class", function (d) { return "bubble-label" + (isETF(d) ? " is-etf" : ""); })
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle")
      .text(function (d) { return d.ticker; });

    bubbles.on("click", function (evt, d) {
      evt.stopPropagation();
      selectTicker(d.ticker);
    });

    simulation.on("tick", function () {
      // clamp to inner box so bubbles never escape the chart frame
      bubbles.attr("transform", function (d) {
        const r = d.radius;
        const x = Math.max(r, Math.min(innerW - r, d.x));
        const y = Math.max(r, Math.min(innerH - r, d.y));
        d.x = x;
        d.y = y;
        return "translate(" + x + "," + y + ")";
      });
    });

    simulation.on("end", function () {
      // labels already positioned on creation; nothing to reposition
    });

    // click on empty chart area: clear selection
    svg.on("click", function () {
      selectTicker(null);
    });

    // Expose for tests / debugging
    window.__stockViz = { simulation: simulation, stocks: stocks };
  }

  // ---------- legend ----------
  function renderLegend(stocks) {
    const legendEl = document.getElementById("legend");
    legendEl.innerHTML = "";
    const countsBySector = d3.rollup(
      stocks,
      function (v) { return v.length; },
      function (d) { return d.sector; }
    );

    SECTOR_ORDER.forEach(function (sector) {
      const item = document.createElement("div");
      item.className = "legend-item";
      const sw = document.createElement("span");
      sw.className = "legend-swatch";
      sw.style.background = SECTOR_COLORS[sector] || "#6b7280";
      const label = document.createElement("span");
      label.textContent = sector + " (" + (countsBySector.get(sector) || 0) + ")";
      item.appendChild(sw);
      item.appendChild(label);
      item.addEventListener("click", function () {
        // toggle dim: highlight only this sector
        const all = legendEl.querySelectorAll(".legend-item");
        const dim = !item.classList.contains("muted");
        all.forEach(function (n) { n.classList.add("muted"); });
        if (dim) {
          item.classList.remove("muted");
        } else {
          all.forEach(function (n) { n.classList.remove("muted"); });
        }
      });
      legendEl.appendChild(item);
    });
  }

  // ---------- data table ----------
  function renderTable(stocks) {
    const tbody = document.getElementById("stock-tbody");
    tbody.innerHTML = "";

    const sorted = stocks.slice().sort(function (a, b) {
      return a.ticker.localeCompare(b.ticker);
    });

    const frag = document.createDocumentFragment();
    sorted.forEach(function (d) {
      const tr = document.createElement("tr");
      tr.setAttribute("data-ticker", d.ticker);

      const tdT = document.createElement("td");
      tdT.className = "ticker-cell";
      tdT.textContent = d.ticker;

      const tdN = document.createElement("td");
      tdN.className = "name-cell";
      tdN.textContent = d.name;
      tdN.title = d.name;

      const tdS = document.createElement("td");
      tdS.className = "sector-cell";
      const chip = document.createElement("span");
      chip.className = "sector-chip";
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = SECTOR_COLORS[d.sector] || "#6b7280";
      chip.appendChild(sw);
      chip.appendChild(document.createTextNode(d.sector));
      tdS.appendChild(chip);

      const tdM = document.createElement("td");
      tdM.className = "num";
      tdM.textContent = formatMarketCap(d.marketCap);

      tr.appendChild(tdT);
      tr.appendChild(tdN);
      tr.appendChild(tdS);
      tr.appendChild(tdM);

      tr.addEventListener("click", function () {
        selectTicker(d.ticker);
      });

      frag.appendChild(tr);
    });
    tbody.appendChild(frag);

    renderLegend(stocks);
  }

  // ---------- selection / linking ----------
  function selectTicker(ticker) {
    state.selectedTicker = ticker;

    // table
    const tbody = document.getElementById("stock-tbody");
    const rows = tbody.querySelectorAll("tr");
    rows.forEach(function (tr) {
      if (ticker && tr.getAttribute("data-ticker") === ticker) {
        tr.classList.add("selected");
        // smooth-scroll the selected row into view
        const wrapper = document.querySelector(".table-wrapper");
        const rowRect = tr.getBoundingClientRect();
        const wrapRect = wrapper.getBoundingClientRect();
        if (rowRect.top < wrapRect.top || rowRect.bottom > wrapRect.bottom) {
          tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      } else {
        tr.classList.remove("selected");
      }
    });

    // bubbles
    d3.selectAll("#bubble-chart .bubble")
      .classed("selected", function (d) { return !!ticker && d.ticker === ticker; });
  }

  // ---------- utils ----------
  function escapeHTML(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
})();
