(function () {
  "use strict";

  const SVG_ID = "#bubble-chart";
  const TABLE_BODY_SELECTOR = "#stocks-table tbody";
  const LEGEND_SELECTOR = "#legend";
  const TOOLTIP_ID = "#tooltip";

  const dataUrl = "data/stock-descriptions.csv";
  const colorScheme = d3.schemeTableau10.concat(d3.schemeSet3);

  const tooltip = d3.select(TOOLTIP_ID);

  function formatMarketCap(value) {
    if (value == null || isNaN(value)) return "—";
    const abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (value / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (value / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (value / 1e3).toFixed(2) + "K";
    return String(value);
  }

  function showTooltip(event, d) {
    if (d.isETF) {
      hideTooltip();
      return;
    }
    tooltip
      .classed("visible", true)
      .attr("aria-hidden", "false")
      .html(
        '<span class="ticker">' + d.ticker + "</span>" +
          '<span class="name">' + d.fullName + "</span>" +
          '<span class="sector">Sector: ' + d.sector + "</span>"
      );
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const pad = 14;
    const node = tooltip.node();
    const rect = node.getBoundingClientRect();
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) {
      x = event.clientX - rect.width - pad;
    }
    if (y + rect.height > window.innerHeight - 8) {
      y = event.clientY - rect.height - pad;
    }
    tooltip.style("left", x + "px").style("top", y + "px");
  }

  function hideTooltip() {
    tooltip.classed("visible", false).attr("aria-hidden", "true");
  }

  function radiusFor(d) {
    if (d.isETF) return 14;
    const minR = 14;
    const maxR = 56;
    const minLog = d3.min(bubbleData, (b) => Math.log10(b.marketCap)) || 9;
    const maxLog = d3.max(bubbleData, (b) => Math.log10(b.marketCap)) || 13;
    const logV = Math.log10(d.marketCap);
    if (maxLog === minLog) return (minR + maxR) / 2;
    const t = (logV - minLog) / (maxLog - minLog);
    return minR + t * (maxR - minR);
  }

  let bubbleData = [];
  let sectors = [];
  let colorScale = null;
  let selectedTicker = null;

  const svg = d3.select(SVG_ID);
  let width = 720;
  let height = 640;

  function renderLegend() {
    const legend = d3.select(LEGEND_SELECTOR).selectAll(".legend-item")
      .data(sectors, (d) => d);
    const enter = legend.enter().append("span").attr("class", "legend-item");
    enter.append("span").attr("class", "legend-swatch");
    enter.append("span").attr("class", "legend-label");

    const merged = enter.merge(legend);
    merged.select(".legend-swatch").style("background-color", (d) => colorScale(d));
    merged.select(".legend-label").text((d) => d);
    legend.exit().remove();
  }

  function buildChart() {
    svg.selectAll("*").remove();

    const widthAttr = parseInt(svg.attr("width"), 10);
    const heightAttr = parseInt(svg.attr("height"), 10);
    const containerRect = svg.node().parentNode.getBoundingClientRect();
    width = Math.max(360, Math.min(820, (containerRect.width && containerRect.width > 80) ? containerRect.width : (widthAttr || 720)));
    height = Math.max(420, Math.round(width * 0.85));
    svg
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("width", width)
      .attr("height", height)
      .style("width", width + "px")
      .style("height", height + "px");

    sectors = Array.from(new Set(bubbleData.map((d) => d.sector)));
    colorScale = d3.scaleOrdinal()
      .domain(sectors)
      .range(colorScheme.slice(0, sectors.length));

    bubbleData.forEach((d) => { d.radius = radiusFor(d); });
    bubbleData.forEach((d) => { d.color = colorScale(d.sector); });

    renderLegend();

    const clusterCenter = (() => {
      const cols = Math.max(1, Math.min(sectors.length, 3));
      const rows = Math.ceil(sectors.length / cols);
      const usableW = width * 0.7;
      const usableH = height * 0.7;
      const startX = (width - usableW) / 2;
      const startY = (height - usableH) / 2;
      return (sector) => {
        const idx = sectors.indexOf(sector);
        const col = idx % cols;
        const row = Math.floor(idx / cols);
        return {
          x: startX + (cols === 1 ? usableW / 2 : (usableW * col) / (cols - 1)),
          y: startY + (rows === 1 ? usableH / 2 : (usableH * row) / (rows - 1)),
        };
      };
    })();

    const cx = bubbleData.map((d) => clusterCenter(d.sector).x);
    const cy = bubbleData.map((d) => clusterCenter(d.sector).y);

    bubbleData.forEach((d, i) => { d.x = cx[i]; d.y = cy[i]; });

    const sim = d3.forceSimulation(bubbleData)
      .force("x", d3.forceX((d) => clusterCenter(d.sector).x).strength(0.25))
      .force("y", d3.forceY((d) => clusterCenter(d.sector).y).strength(0.25))
      .force("collide", d3.forceCollide((d) => d.radius + 4).strength(0.95))
      .force("charge", d3.forceManyBody().strength(-2))
      .stop();

    for (let i = 0; i < 480; i++) sim.tick();

    bubbleData.forEach((d) => {
      d.x = Math.max(d.radius + 4, Math.min(width - d.radius - 4, d.x));
      d.y = Math.max(d.radius + 4, Math.min(height - d.radius - 4, d.y));
    });

    const root = svg.append("g").attr("class", "bubble-root");

    const node = root.selectAll("g.bubble")
      .data(bubbleData, (d) => d.ticker)
      .enter().append("g")
      .attr("class", "bubble")
      .attr("data-ticker", (d) => d.ticker)
      .attr("transform", (d) => "translate(" + d.x + "," + d.y + ")")
      .style("cursor", "pointer")
      .on("mouseover", function (event, d) {
        showTooltip(event, d);
        d3.select(this).raise();
      })
      .on("mousemove", function (event) { moveTooltip(event); })
      .on("mouseout", function () { hideTooltip(); })
      .on("click", function (event, d) { selectTicker(d.ticker, "bubble"); });

    node.append("circle")
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => d.color)
      .attr("stroke", "#222")
      .attr("stroke-width", 1)
      .attr("opacity", (d) => (d.isETF ? 0.85 : 0.92));

    node.append("text")
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-size", (d) => Math.max(10, Math.min(16, d.radius / 2.4)))
      .attr("font-weight", 700)
      .attr("fill", "#0b1220")
      .attr("pointer-events", "none")
      .text((d) => d.ticker);

    node.filter((d) => d.isETF)
      .select("circle")
      .attr("stroke-dasharray", "4 3");
  }

  function buildTable(rows) {
    const tbody = d3.select(TABLE_BODY_SELECTOR);
    const tr = tbody.selectAll("tr").data(rows, (d) => d.ticker);

    const trEnter = tr.enter().append("tr")
      .attr("data-ticker", (d) => d.ticker)
      .style("cursor", "pointer")
      .on("mouseover", function (event, d) {
        if (!d.isETF) showTooltip(event, d);
        highlightOnly(d.ticker, false);
      })
      .on("mouseout", function () { hideTooltip(); highlightOnly(null, false); })
      .on("click", function (event, d) { selectTicker(d.ticker, "table"); });

    trEnter.append("td").attr("class", "ticker-cell");
    trEnter.append("td").attr("class", "name-cell");
    trEnter.append("td").attr("class", "sector-cell");
    trEnter.append("td").attr("class", "marketcap-cell");

    const merged = trEnter.merge(tr);
    merged.select(".ticker-cell").text((d) => d.ticker);
    merged.select(".name-cell").text((d) => d.fullName);
    merged.select(".sector-cell").text((d) => d.sector);
    merged.select(".marketcap-cell").text((d) => formatMarketCap(d.marketCap));

    tr.exit().remove();
  }

  function highlightOnly(ticker, persist) {
    const useTicker = persist ? ticker : null;
    const nodes = svg.selectAll("g.bubble");
    nodes.classed("highlight", (d) => !!useTicker && d.ticker === useTicker);
    nodes.classed("dimmed", (d) => !!useTicker && d.ticker !== useTicker);
    nodes.classed("text-dimmed", (d) => !!useTicker && d.ticker !== useTicker);

    const rows = d3.select(TABLE_BODY_SELECTOR).selectAll("tr");
    rows.classed("selected", (d) => !!useTicker && d.ticker === useTicker);
    rows.classed("dimmed", (d) => !!useTicker && d.ticker !== useTicker);
  }

  function clearSelection() {
    selectedTicker = null;
    highlightOnly(null, false);
  }

  function selectTicker(ticker, source) {
    if (!ticker) return;
    if (selectedTicker === ticker) {
      clearSelection();
    } else {
      selectedTicker = ticker;
      highlightOnly(ticker, true);
    }
    if (source === "bubble" && selectedTicker) {
      const row = document.querySelector("#stocks-table tbody tr[data-ticker=\"" + selectedTicker + "\"]");
      if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function parseRow(d) {
    const ticker = (d.ticker || "").trim();
    const sector = (d.sector || "").trim() || "Unknown";
    const fullName = (d["full name"] || "").trim();
    const marketCap = d.marketCap && d.marketCap !== "" ? +d.marketCap : null;
    return {
      ticker,
      sector,
      fullName,
      marketCap,
      isETF: sector.toLowerCase() === "etf" || marketCap == null,
    };
  }

  function init() {
    d3.csv(dataUrl).then((raw) => {
      bubbleData = raw.map(parseRow).filter((d) => d.ticker);
      buildTable(bubbleData);
      buildChart();
    }).catch((err) => {
      console.error("Failed to load data:", err);
    });

    window.addEventListener("resize", () => {
      if (bubbleData.length) buildChart();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
