(function () {
  "use strict";

  const SECTOR_COLORS = {
    "Information Technology": "#2563eb",
    "Industry": "#ea580c",
    "Energy": "#b45309",
    "Financial": "#059669",
    "ETF": "#7c3aed"
  };

  const ETF_RADIUS = 16;
  const MIN_COMPANY_RADIUS = 12;
  const MAX_COMPANY_RADIUS = 46;

  const STOCK_CSV = "data/stock-descriptions.csv";

  const formatMarketCap = function (value) {
    if (value == null || isNaN(value) || value <= 0) return "N/A";
    const abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (value / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (value / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (value / 1e3).toFixed(2) + "K";
    return value.toString();
  };

  const escapeHtml = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  const parseRow = function (d) {
    const marketCap = d.marketCap && d.marketCap.trim() !== "" && !isNaN(+d.marketCap)
      ? +d.marketCap
      : null;
    return {
      ticker: d.ticker,
      sector: d.sector,
      name: d["full name"],
      marketCap: marketCap,
      isETF: d.sector === "ETF"
    };
  };

  const computeClusterLayout = function (sectors, width, height) {
    const n = sectors.length;
    const cols = Math.ceil(Math.sqrt(n));
    const rows = Math.ceil(n / cols);
    const padX = width * 0.18;
    const padY = height * 0.20;
    const stepX = (width - 2 * padX) / Math.max(1, cols - 1);
    const stepY = (height - 2 * padY) / Math.max(1, rows - 1);
    const layout = {};
    sectors.forEach(function (s, i) {
      const c = i % cols;
      const r = Math.floor(i / cols);
      layout[s] = {
        x: padX + c * stepX,
        y: padY + r * stepY
      };
    });
    return layout;
  };

  const state = {
    rows: [],
    sectors: [],
    colorScale: null,
    radiusScale: null,
    selectedTicker: null,
    simulation: null,
    svg: null,
    gBubbles: null,
    width: 0,
    height: 0
  };

  const tooltip = (function () {
    let t = document.getElementById("tooltip");
    if (!t) {
      t = document.createElement("div");
      t.id = "tooltip";
      t.setAttribute("role", "tooltip");
      document.body.appendChild(t);
    }
    return d3.select(t);
  })();

  const showTooltip = function (event, row) {
    if (row.isETF) return;
    tooltip
      .html(
        '<span class="ticker">' + escapeHtml(row.ticker) + '</span>' +
        '<span class="name">' + escapeHtml(row.name) + '</span>' +
        '<div class="meta">Sector: ' + escapeHtml(row.sector) + '</div>'
      )
      .classed("visible", true);
    moveTooltip(event);
  };

  const moveTooltip = function (event) {
    const x = event.clientX + 14;
    const y = event.clientY + 14;
    tooltip.style("left", x + "px").style("top", y + "px");
  };

  const hideTooltip = function () {
    tooltip.classed("visible", false);
  };

  const highlightRow = function (ticker) {
    d3.selectAll("tr.row").classed("highlight", function (d) {
      return d && d.ticker === ticker;
    });
  };

  const clearRowHighlight = function () {
    d3.selectAll("tr.row").classed("highlight", false);
  };

  const highlightBubble = function (ticker) {
    state.gBubbles.selectAll("circle.bubble")
      .classed("selected", function (d) {
        return d.ticker === ticker;
      })
      .attr("stroke", function (d) {
        return d.ticker === ticker ? "#111827" : "#ffffff";
      })
      .attr("stroke-width", function (d) {
        return d.ticker === ticker ? 3 : 1.5;
      });
  };

  const clearBubbleHighlight = function () {
    state.gBubbles.selectAll("circle.bubble")
      .classed("selected", false)
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5);
  };

const renderTable = function () {
    const sortedRows = state.rows.slice().sort(function (a, b) {
      return a.ticker.localeCompare(b.ticker);
    });

    const tbody = d3.select("#stock-table-body");
    const rowSel = tbody.selectAll("tr.row")
      .data(sortedRows, function (d) { return d.ticker; });

    rowSel.exit().remove();

    const rowEnter = rowSel.enter().append("tr").attr("class", "row");

    rowEnter.append("td").attr("class", "ticker");
    rowEnter.append("td").attr("class", "name");
    rowEnter.append("td").attr("class", "sector-cell");
    rowEnter.append("td").attr("class", "numeric market-cap");

    const merged = rowEnter.merge(rowSel);

    merged.order();

    merged
      .on("mouseenter", function (event, d) {
        if (!state.selectedTicker) highlightRow(d.ticker);
      })
      .on("mouseleave", function () {
        if (!state.selectedTicker) clearRowHighlight();
        else highlightRow(state.selectedTicker);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        if (state.selectedTicker === d.ticker) {
          state.selectedTicker = null;
          clearRowHighlight();
          clearBubbleHighlight();
        } else {
          state.selectedTicker = d.ticker;
          highlightRow(d.ticker);
          highlightBubble(d.ticker);
        }
      });

    merged.select("td.ticker").text(function (d) { return d.ticker; });
    merged.select("td.name").text(function (d) { return d.name; });
    merged.select("td.sector-cell").html(function (d) {
      return '<span class="sector-dot" style="background:' + state.colorScale(d.sector) + '"></span>' +
        escapeHtml(d.sector);
    });
    merged.select("td.market-cap").text(function (d) { return formatMarketCap(d.marketCap); });
  };

  const renderLegend = function () {
    const legend = d3.select("#legend");
    legend.selectAll("*").remove();
    state.sectors.forEach(function (sector) {
      const item = legend.append("div")
        .attr("class", "legend-item")
        .attr("data-sector", sector);
      item.append("span")
        .attr("class", "legend-swatch")
        .style("background", state.colorScale(sector));
      item.append("span").text(sector);
    });
  };

  const renderChart = function () {
    const container = document.getElementById("bubble-chart");
    const svgOnly = container.querySelector("svg");
    if (svgOnly) svgOnly.remove();

    const rect = container.getBoundingClientRect();
    const width = Math.max(640, Math.floor(rect.width));
    const height = Math.max(560, Math.floor(rect.height || 620));
    state.width = width;
    state.height = height;

    const svg = d3.select(container)
      .append("svg")
      .attr("width", width)
      .attr("height", height)
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("preserveAspectRatio", "xMidYMid meet");

    state.svg = svg;
    state.gBubbles = svg.append("g").attr("class", "bubbles");

    const clusterLayout = computeClusterLayout(state.sectors, width, height);
    const clusterG = svg.append("g").attr("class", "cluster-titles");

    state.sectors.forEach(function (sector) {
      clusterG.append("text")
        .attr("class", "cluster-title")
        .attr("x", clusterLayout[sector].x)
        .attr("y", clusterLayout[sector].y - 90)
        .attr("text-anchor", "middle")
        .style("font-size", "12px")
        .style("font-weight", "600")
        .style("fill", state.colorScale(sector))
        .style("opacity", 0.85)
        .text(sector);
    });

    const marketCaps = state.rows.filter(function (r) { return !r.isETF && r.marketCap; }).map(function (r) { return r.marketCap; });
    const minCap = d3.min(marketCaps);
    const maxCap = d3.max(marketCaps);

    state.radiusScale = d3.scaleSqrt()
      .domain([minCap, maxCap])
      .range([MIN_COMPANY_RADIUS, MAX_COMPANY_RADIUS]);

    const nodes = state.rows.map(function (r) {
      const cluster = clusterLayout[r.sector];
      return {
        ticker: r.ticker,
        sector: r.sector,
        name: r.name,
        marketCap: r.marketCap,
        isETF: r.isETF,
        radius: r.isETF ? ETF_RADIUS : state.radiusScale(r.marketCap || minCap),
        cx: cluster.x,
        cy: cluster.y,
        color: state.colorScale(r.sector)
      };
    });

    state.nodes = nodes;

    const ticked = function () {
      const bubbles = state.gBubbles.selectAll("g.bubble-group");
      bubbles.attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });
    };

    const sim = d3.forceSimulation(nodes)
      .force("collide", d3.forceCollide().radius(function (d) { return d.radius + 1.5; }).strength(0.95).iterations(2))
      .force("charge", d3.forceManyBody().strength(-2))
      .force("x", d3.forceX().x(function (d) { return clusterLayout[d.sector].x; }).strength(0.35))
      .force("y", d3.forceY().y(function (d) { return clusterLayout[d.sector].y; }).strength(0.35))
      .force("center", d3.forceCenter(width / 2, height / 2).strength(0.06))
      .alpha(1)
      .alphaDecay(0.025)
      .on("tick", ticked);

    state.simulation = sim;

    const group = state.gBubbles.selectAll("g.bubble-group")
      .data(nodes, function (d) { return d.ticker; });

    group.exit().remove();

    const groupEnter = group.enter().append("g")
      .attr("class", "bubble-group")
      .style("cursor", "pointer");

    groupEnter.append("circle").attr("class", "bubble");
    groupEnter.append("title");
    groupEnter.append("text").attr("class", "bubble-label");

    const merged = groupEnter.merge(group);

    merged.select("circle.bubble")
      .attr("r", function (d) { return d.radius; })
      .attr("fill", function (d) { return d.color; })
      .attr("fill-opacity", 0.78)
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5);

    merged.select("text.bubble-label")
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .style("font-size", function (d) { return Math.max(9, Math.min(13, d.radius * 0.55)); })
      .style("font-weight", "600")
      .style("fill", "#ffffff")
      .style("pointer-events", "none")
      .text(function (d) { return d.ticker; });

    merged
      .on("mouseenter", function (event, d) {
        highlightRow(d.ticker);
        showTooltip(event, d);
      })
      .on("mousemove", function (event) {
        moveTooltip(event);
      })
      .on("mouseleave", function (event, d) {
        hideTooltip();
        if (!state.selectedTicker) clearRowHighlight();
        else highlightRow(state.selectedTicker);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        if (state.selectedTicker === d.ticker) {
          state.selectedTicker = null;
          clearRowHighlight();
          clearBubbleHighlight();
        } else {
          state.selectedTicker = d.ticker;
          highlightRow(d.ticker);
          highlightBubble(d.ticker);
        }
      });

    state.svg.on("click", function () {
      if (state.selectedTicker) {
        state.selectedTicker = null;
        clearRowHighlight();
        clearBubbleHighlight();
      }
    });
  };

  const init = function () {
    d3.csv(STOCK_CSV).then(function (raw) {
      const rows = raw.map(parseRow).filter(function (r) { return r.ticker; });
      state.rows = rows;
      state.sectors = Array.from(new Set(rows.map(function (r) { return r.sector; })));
      state.colorScale = d3.scaleOrdinal()
        .domain(state.sectors)
        .range(state.sectors.map(function (s) { return SECTOR_COLORS[s] || "#6b7280"; }));

      renderChart();
      renderLegend();
      renderTable();

      let resizeTimer = null;
      window.addEventListener("resize", function () {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          renderChart();
        }, 200);
      });
    }).catch(function (err) {
      console.error("Failed to load stock data:", err);
      const container = document.getElementById("bubble-chart");
      container.innerHTML = '<div style="color:#b91c1c;padding:18px;">Failed to load data: ' +
        escapeHtml(err && err.message ? err.message : String(err)) + '</div>';
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
