(function () {
  "use strict";

  const DATA_PATHS = {
    descriptions: "data/stock-descriptions.csv"
  };

  const SECTOR_ORDER = [
    "Information Technology",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financial",
    "Health Care",
    "Industry",
    "Materials",
    "Real Estate",
    "Utilities",
    "ETF"
  ];

  const SECTOR_COLORS = {
    "Information Technology": "#1f77b4",
    "Communication Services": "#9467bd",
    "Consumer Discretionary": "#e377c2",
    "Consumer Staples": "#8c564b",
    "Energy": "#d62728",
    "Financial": "#2ca02c",
    "Health Care": "#17becf",
    "Industry": "#bcbd22",
    "Materials": "#ff7f0e",
    "Real Estate": "#7f7f7f",
    "Utilities": "#9edae5",
    "ETF": "#aec7e8"
  };

  const state = {
    stocks: [],
    byTicker: new Map(),
    selected: null
  };

  function formatMarketCap(value) {
    if (value === null || value === undefined || value === "" || isNaN(+value)) {
      return "N/A";
    }
    const n = +value;
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2).replace(/\.00$/, "") + "T";
    if (abs >= 1e9) return (n / 1e9).toFixed(2).replace(/\.00$/, "") + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(2).replace(/\.00$/, "") + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(2).replace(/\.00$/, "") + "K";
    return String(n);
  }

  function parseCSV(text) {
    const rows = [];
    let field = "";
    let row = [];
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') {
            field += '"';
            i++;
          } else {
            inQuotes = false;
          }
        } else {
          field += c;
        }
      } else {
        if (c === '"') {
          inQuotes = true;
        } else if (c === ",") {
          row.push(field);
          field = "";
        } else if (c === "\n") {
          row.push(field);
          rows.push(row);
          row = [];
          field = "";
        } else if (c === "\r") {
          // skip; \r\n handled by \n
        } else {
          field += c;
        }
      }
    }
    if (field.length > 0 || row.length > 0) {
      row.push(field);
      rows.push(row);
    }
    if (rows.length === 0) return [];
    const headers = rows[0];
    return rows.slice(1).map(function (r) {
      const obj = {};
      headers.forEach(function (h, idx) {
        obj[h] = r[idx] !== undefined ? r[idx] : "";
      });
      return obj;
    });
  }

  function loadData() {
    return fetch(DATA_PATHS.descriptions)
      .then(function (res) { return res.text(); })
      .then(function (text) {
        const records = parseCSV(text);
        state.stocks = records.map(function (r) {
          const ticker = (r["ticker"] || "").trim();
          const sector = (r["sector"] || "").trim() || "ETF";
          const name = (r["full name"] || "").trim();
          const mcRaw = (r["marketCap"] || "").trim();
          const marketCap = mcRaw === "" ? null : +mcRaw;
          return {
            ticker: ticker,
            sector: sector,
            name: name,
            marketCap: marketCap,
            country: (r["country"] || "").trim(),
            website: (r["website"] || "").trim()
          };
        });
        state.byTicker = new Map(state.stocks.map(function (s) { return [s.ticker, s]; }));
      });
  }

  function getSectors() {
    const seen = new Set();
    const out = [];
    state.stocks.forEach(function (s) {
      if (!seen.has(s.sector)) {
        seen.add(s.sector);
        out.push(s.sector);
      }
    });
    out.sort(function (a, b) {
      const ia = SECTOR_ORDER.indexOf(a);
      const ib = SECTOR_ORDER.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
    return out;
  }

  function colorFor(sector) {
    return SECTOR_COLORS[sector] || "#666";
  }

  function computeMarketCapStats(stocks) {
    const mcs = stocks
      .map(function (s) { return s.marketCap; })
      .filter(function (v) { return v !== null && v !== undefined && !isNaN(v) && v > 0; });
    if (mcs.length === 0) return { min: 0, max: 0 };
    return { min: d3.min(mcs), max: d3.max(mcs) };
  }

  function radiusFor(stock, stats) {
    const minR = 10;
    const maxR = 60;
    if (stock.marketCap === null || stock.marketCap === undefined || isNaN(stock.marketCap)) {
      return minR;
    }
    const mc = +stock.marketCap;
    if (mc <= 0) return minR;
    if (stats.max === stats.min) return (minR + maxR) / 2;
    // sqrt scale so area scales linearly with market cap
    const t = Math.sqrt((mc - stats.min) / (stats.max - stats.min));
    return minR + t * (maxR - minR);
  }

  function renderLegend(sectors) {
    const legend = document.getElementById("legend");
    legend.innerHTML = "";
    sectors.forEach(function (sector) {
      const item = document.createElement("div");
      item.className = "legend-item";
      const sw = document.createElement("span");
      sw.className = "legend-swatch";
      sw.style.background = colorFor(sector);
      const label = document.createElement("span");
      label.textContent = sector;
      item.appendChild(sw);
      item.appendChild(label);
      legend.appendChild(item);
    });
  }

  function renderTable() {
    const tbody = document.querySelector("#stocks-table tbody");
    tbody.innerHTML = "";
    state.stocks.forEach(function (s) {
      const tr = document.createElement("tr");
      tr.dataset.ticker = s.ticker;
      const cells = [
        s.ticker,
        s.name,
        s.sector,
        formatMarketCap(s.marketCap)
      ];
      cells.forEach(function (val) {
        const td = document.createElement("td");
        td.textContent = val;
        tr.appendChild(td);
      });
      tr.addEventListener("click", function () {
        selectStock(s.ticker, { from: "table" });
      });
      tbody.appendChild(tr);
    });
  }

  function ensureTooltip() {
    let tip = document.querySelector(".tooltip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tooltip";
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTooltip(stock, evt) {
    if (!stock.country && !stock.website && stock.marketCap === null) {
      // ETF with no tooltip info
      return;
    }
    const tip = ensureTooltip();
    tip.innerHTML =
      '<span class="tip-ticker">' + stock.ticker + "</span>" +
      '<span class="tip-sector">' + stock.sector + "</span><br/>" +
      '<span>' + stock.name + "</span>";
    tip.classList.add("visible");
    moveTooltip(evt);
  }

  function moveTooltip(evt) {
    const tip = ensureTooltip();
    const padding = 12;
    const rect = tip.getBoundingClientRect();
    let x = evt.clientX + padding;
    let y = evt.clientY + padding;
    if (x + rect.width + padding > window.innerWidth) {
      x = evt.clientX - rect.width - padding;
    }
    if (y + rect.height + padding > window.innerHeight) {
      y = evt.clientY - rect.height - padding;
    }
    tip.style.left = Math.max(4, x) + "px";
    tip.style.top = Math.max(4, y) + "px";
  }

  function hideTooltip() {
    const tip = ensureTooltip();
    tip.classList.remove("visible");
  }

  function selectStock(ticker, opts) {
    opts = opts || {};
    state.selected = ticker;
    // highlight bubbles
    d3.selectAll(".bubble").classed("selected", function (d) {
      return d.ticker === ticker;
    });
    d3.selectAll(".bubble").classed("dimmed", function (d) {
      return ticker !== null && d.ticker !== ticker;
    });
    d3.selectAll(".bubble-label").classed("dimmed", function (d) {
      return ticker !== null && d.ticker !== ticker;
    });

    // highlight row
    d3.selectAll("#stocks-table tbody tr").classed("highlighted", function () {
      return this.dataset.ticker === ticker;
    });
    if (opts.from !== "table") {
      const row = document.querySelector("#stocks-table tbody tr[data-ticker=\"" + CSS.escape(ticker) + "\"]");
      if (row) {
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  function clearSelection() {
    state.selected = null;
    d3.selectAll(".bubble").classed("selected", false).classed("dimmed", false);
    d3.selectAll(".bubble-label").classed("dimmed", false);
    d3.selectAll("#stocks-table tbody tr").classed("highlighted", false);
    hideTooltip();
  }

  function renderChart(sectors) {
    const container = document.getElementById("bubble-chart");
    container.innerHTML = "";

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 500;

    const svg = d3.select(container)
      .append("svg")
      .attr("viewBox", [0, 0, width, height])
      .attr("preserveAspectRatio", "xMidYMid meet");

    // Compute market cap stats for radius calculation
    const stats = computeMarketCapStats(state.stocks);

    // Center positions for each sector cluster (arranged in a grid)
    const cols = Math.ceil(Math.sqrt(sectors.length));
    const rows = Math.ceil(sectors.length / cols);
    const padX = width * 0.18;
    const padY = height * 0.18;
    const usableW = width - 2 * padX;
    const usableH = height - 2 * padY;

    const centers = {};
    sectors.forEach(function (sector, idx) {
      const c = idx % cols;
      const r = Math.floor(idx / cols);
      const x = padX + (cols === 1 ? usableW / 2 : (c * (usableW / (cols - 1))));
      const y = padY + (rows === 1 ? usableH / 2 : (r * (usableH / (rows - 1))));
      centers[sector] = { x: x, y: y };
    });

    // Pre-compute radii
    const nodes = state.stocks.map(function (s) {
      return {
        ticker: s.ticker,
        sector: s.sector,
        name: s.name,
        marketCap: s.marketCap,
        country: s.country,
        website: s.website,
        radius: radiusFor(s, stats),
        x: centers[s.sector].x + (Math.random() - 0.5) * 40,
        y: centers[s.sector].y + (Math.random() - 0.5) * 40
      };
    });

    // Compute largest radius to calibrate collide
    const allRadii = nodes.map(function (n) { return n.radius; });
    const maxR = d3.max(allRadii) || 30;

    const simulation = d3.forceSimulation(nodes)
      .force("x", d3.forceX(function (d) { return centers[d.sector].x; }).strength(0.18))
      .force("y", d3.forceY(function (d) { return centers[d.sector].y; }).strength(0.18))
      .force("collide", d3.forceCollide(function (d) { return d.radius + 2; }).iterations(3))
      .force("charge", d3.forceManyBody().strength(-5))
      .stop();

    const iterations = 240;
    for (let i = 0; i < iterations; i++) {
      simulation.tick();
    }

    // Final clamp to keep within chart bounds with padding for the largest radius
    const boundPad = maxR + 4;
    nodes.forEach(function (n) {
      n.x = Math.max(boundPad, Math.min(width - boundPad, n.x));
      n.y = Math.max(boundPad, Math.min(height - boundPad, n.y));
    });

    // Draw bubbles
    const bubbleGroups = svg.selectAll(".bubble-group")
      .data(nodes, function (d) { return d.ticker; })
      .enter()
      .append("g")
      .attr("class", "bubble-group")
      .attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });

    bubbleGroups.append("circle")
      .attr("class", "bubble")
      .attr("r", function (d) { return d.radius; })
      .attr("fill", function (d) { return colorFor(d.sector); })
      .on("mouseover", function (event, d) {
        showTooltip(d, event);
      })
      .on("mousemove", function (event) {
        moveTooltip(event);
      })
      .on("mouseout", function () {
        hideTooltip();
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        if (state.selected === d.ticker) {
          clearSelection();
        } else {
          selectStock(d.ticker, { from: "chart" });
        }
      });

    bubbleGroups.append("text")
      .attr("class", "bubble-label")
      .attr("dy", 0)
      .text(function (d) { return d.ticker; });

    svg.on("click", function () {
      clearSelection();
    });
  }

  function init() {
    loadData().then(function () {
      const sectors = getSectors();
      renderLegend(sectors);
      renderTable();
      renderChart(sectors);

      let resizeTimer = null;
      window.addEventListener("resize", function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          const newSectors = getSectors();
          renderChart(newSectors);
          // re-apply selection if any
          if (state.selected) {
            selectStock(state.selected, { from: "resize" });
          }
        }, 150);
      });
    }).catch(function (err) {
      console.error("Failed to load data:", err);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
