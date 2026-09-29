(function () {
  "use strict";

  const WIDTH = 900;
  const HEIGHT = 640;

  const ETF_UNIFORM_RADIUS = 14;
  const ETF_MIN_MARKET_CAP_RADIUS = 18;
  const ETF_MAX_MARKET_CAP_RADIUS = 36;

  const COLOR_SCALE = d3.scaleOrdinal()
    .domain(["Information Technology", "Industry", "Energy", "Financial", "ETF"])
    .range(["#2563eb", "#ca8a04", "#dc2626", "#16a34a", "#7c3aed"]);

  const CLUSTER_CENTERS = (() => {
    const cx = WIDTH / 2;
    const cy = HEIGHT / 2;
    const sectors = ["Information Technology", "Industry", "Energy", "Financial", "ETF"];
    const cols = 3;
    const dx = 250;
    const dy = 180;
    const startX = cx - dx;
    const startY = cy - dy;
    const map = {};
    sectors.forEach((s, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      map[s] = { x: startX + col * dx, y: startY + row * dy };
    });
    return map;
  })();

  const formatMarketCap = (value) => {
    if (value === null || value === undefined || value === "" || isNaN(+value)) {
      return "N/A";
    }
    const n = +value;
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(2) + "K";
    return String(n);
  };

  const tooltip = d3.select("#tooltip");

  function showTooltip(event, d) {
    if (d._isETF) return;
    tooltip
      .html(
        `<span class="tt-ticker">${d.ticker}</span>` +
        `<span class="tt-name">${d.fullName}</span>` +
        `<span class="tt-sector">Sector: ${d.sector}</span>`
      )
      .classed("visible", true);
    moveTooltip(event);
  }

  function moveTooltip(event) {
    const padding = 14;
    const rect = tooltip.node().getBoundingClientRect();
    let x = event.clientX + padding;
    let y = event.clientY + padding;
    if (x + rect.width > window.innerWidth) {
      x = event.clientX - rect.width - padding;
    }
    if (y + rect.height > window.innerHeight) {
      y = event.clientY - rect.height - padding;
    }
    tooltip.style("left", x + "px").style("top", y + "px");
  }

  function hideTooltip() {
    tooltip.classed("visible", false);
  }

  function renderLegend(sectors) {
    const legend = d3.select("#legend");
    legend.selectAll("*").remove();
    sectors.forEach((sector) => {
      const item = legend.append("div").attr("class", "legend-item");
      item
        .append("div")
        .attr("class", "legend-swatch")
        .style("background-color", COLOR_SCALE(sector));
      item.append("span").text(sector);
    });
  }

  function renderTable(stocks, onRowClick) {
    const tbody = d3.select("#stock-table tbody");
    const rows = tbody
      .selectAll("tr")
      .data(stocks, (d) => d.ticker)
      .join("tr")
      .attr("class", "stock-row")
      .attr("data-ticker", (d) => d.ticker)
      .on("click", (event, d) => {
        onRowClick(d.ticker);
      });

    rows.selectAll("td").remove();

    rows
      .append("td")
      .attr("class", "ticker-cell")
      .text((d) => d.ticker);

    rows
      .append("td")
      .attr("class", "name-cell")
      .text((d) => d.fullName);

    rows
      .append("td")
      .attr("class", "sector-cell")
      .append("span")
      .attr("class", "sector-pill")
      .style("background-color", (d) => COLOR_SCALE(d.sector))
      .text((d) => d.sector);

    rows
      .append("td")
      .attr("class", "num-cell")
      .text((d) => formatMarketCap(d.marketCap));

    return rows;
  }

  function highlightSelection(ticker) {
    d3.selectAll("#bubble-chart circle.bubble").classed("selected", function (d) {
      return d && d.ticker === ticker;
    });
    d3.selectAll("#stock-table tbody tr").each(function (d) {
      const isMatch = d && d.ticker === ticker;
      d3.select(this)
        .classed("selected", !!isMatch)
        .classed("highlight", !!isMatch)
        .classed("active", !!isMatch)
        .style("background-color", isMatch ? "#fef3c7" : null)
        .style("outline", isMatch ? "2px solid #f59e0b" : null);
      if (isMatch) {
        this.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
  }

  function build(stocksRaw) {
    const stocks = stocksRaw
      .map((d) => ({
        ticker: d.ticker,
        sector: d.sector,
        fullName: d["full name"] || d.fullName || "",
        marketCap: d.marketCap && d.marketCap !== "" ? +d.marketCap : null,
        _isETF: !(d.marketCap && d.marketCap !== ""),
        _row: d,
      }))
      .filter((d) => d.ticker);

    const stocksWithCap = stocks.filter((s) => !s._isETF);
    const stocksNoCap = stocks.filter((s) => s._isETF);

    const radiusScale = d3.scaleSqrt()
      .domain(d3.extent(stocksWithCap, (s) => s.marketCap))
      .range([ETF_MIN_MARKET_CAP_RADIUS, ETF_MAX_MARKET_CAP_RADIUS]);

    stocksWithCap.forEach((s) => {
      s.radius = radiusScale(s.marketCap);
    });
    stocksNoCap.forEach((s) => {
      s.radius = ETF_UNIFORM_RADIUS;
    });

    const sectorOrder = stocks
      .map((s) => s.sector)
      .filter((v, i, a) => a.indexOf(v) === i);

    const svg = d3.select("#bubble-chart");

    svg.selectAll("*").remove();

    const clusterGroups = svg
      .selectAll("g.cluster")
      .data(sectorOrder.map((sector) => [sector, stocks.filter((s) => s.sector === sector)]))
      .join("g")
      .attr("class", "cluster")
      .attr("data-sector", (d) => d[0]);

    const nodes = stocks.map((s) => Object.assign({}, s));

    const simulation = d3
      .forceSimulation(nodes)
      .force(
        "x",
        d3.forceX((d) => CLUSTER_CENTERS[d.sector].x).strength(0.4)
      )
      .force(
        "y",
        d3.forceY((d) => CLUSTER_CENTERS[d.sector].y).strength(0.4)
      )
      .force(
        "collide",
        d3.forceCollide((d) => d.radius + 4).strength(0.9)
      )
      .force("charge", d3.forceManyBody().strength(-25))
      .force(
        "center",
        d3.forceCenter(WIDTH / 2, HEIGHT / 2).strength(0.04)
      )
      .stop();

    for (let i = 0; i < 300; ++i) simulation.tick();

    clusterGroups.each(function (groupData) {
      const group = d3.select(this);
      const groupNodes = nodes.filter((n) => n.sector === groupData[0]);
      const minX = d3.min(groupNodes, (n) => n.x - n.radius) - 8;
      const maxX = d3.max(groupNodes, (n) => n.x + n.radius) + 8;
      const minY = d3.min(groupNodes, (n) => n.y - n.radius) - 8;
      const maxY = d3.max(groupNodes, (n) => n.y + n.radius) + 8;
      group
        .append("rect")
        .attr("class", "cluster-bg")
        .attr("x", minX)
        .attr("y", minY)
        .attr("width", maxX - minX)
        .attr("height", maxY - minY)
        .attr("rx", 12)
        .attr("ry", 12)
        .attr("fill", COLOR_SCALE(groupData[0]))
        .attr("fill-opacity", 0.08)
        .attr("stroke", COLOR_SCALE(groupData[0]))
        .attr("stroke-opacity", 0.3)
        .attr("stroke-dasharray", "4 4");
    });

    const bubbles = clusterGroups
      .selectAll("circle.bubble")
      .data(
        (d) => nodes.filter((n) => n.sector === d[0]),
        (d) => d.ticker
      )
      .join("circle")
      .attr("class", (d) => "bubble" + (d._isETF ? " etf" : ""))
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => COLOR_SCALE(d.sector))
      .attr("fill-opacity", (d) => (d._isETF ? 0.85 : 0.92))
      .attr("stroke-dasharray", (d) => (d._isETF ? "3 3" : null))
      .attr("data-ticker", (d) => d.ticker)
      .on("mouseover", function (event, d) {
        showTooltip(event, d);
      })
      .on("mousemove", function (event) {
        moveTooltip(event);
      })
      .on("mouseout", function () {
        hideTooltip();
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        highlightSelection(d.ticker);
      });

    bubbles.attr("cx", (d) => d.x).attr("cy", (d) => d.y);

    const labels = clusterGroups
      .selectAll("text.bubble-label")
      .data(
        (d) => nodes.filter((n) => n.sector === d[0]),
        (d) => d.ticker
      )
      .join("text")
      .attr("class", (d) => "bubble-label" + (d._isETF ? " etf" : ""))
      .text((d) => d.ticker)
      .attr("x", (d) => d.x)
      .attr("y", (d) => d.y);

    renderLegend(sectorOrder);

    const tableRows = renderTable(stocks, (ticker) => {
      highlightSelection(ticker);
    });

    return { svg, bubbles, labels, tableRows, simulation, stocks };
  }

  function init() {
    d3.csv("data/stock-descriptions.csv").then((rows) => {
      try {
        build(rows);
      } catch (err) {
        console.error("Visualization build failed:", err);
        d3.select("#chart-panel").append("div").text("Error: " + err.message);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();