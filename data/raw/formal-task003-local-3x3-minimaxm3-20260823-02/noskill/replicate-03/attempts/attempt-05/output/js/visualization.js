/* global d3 */
(function () {
  "use strict";

  const SECTOR_COLORS = {
    "Information Technology": "#4c78a8",
    Industry: "#f58518",
    Energy: "#e45756",
    Financial: "#72b7b2",
    ETF: "#9d7bb0",
  };

  const SECTOR_KEYS = Object.keys(SECTOR_COLORS);

  const FORMAT_MCAP = function (v) {
    if (v === null || v === undefined || v === "") return "N/A";
    const n = +v;
    if (!isFinite(n) || n <= 0) return "N/A";
    if (n >= 1e12) return (n / 1e12).toFixed(2).replace(/\.?0+$/, "") + "T";
    if (n >= 1e9) return (n / 1e9).toFixed(2).replace(/\.?0+$/, "") + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
    return n.toFixed(0);
  };

  const ESCAPE_HTML = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  function buildVisualization() {
    const chartPanel = document.getElementById("chart-panel");
    const bubbleSvg = d3.select("#bubble-chart");
    const legendEl = document.getElementById("legend");
    const tooltip = document.getElementById("tooltip");
    const tableBody = d3.select("#stocks-table tbody");

    function showTooltip() {
      tooltip.classList.add("visible");
    }
    function hideTooltip() {
      tooltip.classList.remove("visible");
    }
    function isTooltipVisible() {
      return tooltip.classList.contains("visible");
    }
    function positionTooltip(event) {
      const panelRect = chartPanel.getBoundingClientRect();
      const x = event.clientX - panelRect.left + 12;
      const y = event.clientY - panelRect.top + 12;
      tooltip.style.left = x + "px";
      tooltip.style.top = y + "px";
    }

    let chartWidth = 600;
    let chartHeight = 500;

    function measure() {
      const node = bubbleSvg.node();
      const rect = node.getBoundingClientRect();
      chartWidth = Math.max(320, rect.width);
      chartHeight = Math.max(320, rect.height);
    }

    measure();

    const svg = bubbleSvg.append("svg")
      .attr("width", chartWidth)
      .attr("height", chartHeight)
      .attr("viewBox", [0, 0, chartWidth, chartHeight]);

    const bubbleGroup = svg.append("g").attr("class", "bubbles");

    d3.csv("data/stock-descriptions.csv").then(function (rawRows) {
      const rows = rawRows.map(function (r) {
        const sector = (r.sector || "").trim();
        const mcapRaw = (r.marketCap || "").trim();
        const mcap = mcapRaw === "" ? null : +mcapRaw;
        return {
          ticker: (r.ticker || "").trim(),
          sector: sector,
          name: (r["full name"] || "").trim(),
          marketCap: mcap,
          hasTooltip: isFinite(mcap) && mcap > 0,
        };
      }).filter(function (r) {
        return r.ticker !== "";
      });

      rows.forEach(function (r) {
        r.color = SECTOR_COLORS[r.sector] || "#888";
      });

      const mcaps = rows.map(function (r) { return r.marketCap; }).filter(function (v) {
        return v != null && isFinite(v) && v > 0;
      });
      const minCap = mcaps.length ? d3.min(mcaps) : 0;
      const maxCap = mcaps.length ? d3.max(mcaps) : 1;

      const radiusScale = d3.scaleSqrt()
        .domain([minCap, maxCap])
        .range([18, 60]);

      const etfRadius = 18;

      rows.forEach(function (r) {
        r.radius = (r.marketCap && isFinite(r.marketCap) && r.marketCap > 0)
          ? radiusScale(r.marketCap)
          : etfRadius;
      });

      const sectorsPresent = Array.from(new Set(rows.map(function (r) { return r.sector; })));
      sectorsPresent.sort(function (a, b) {
        const ai = SECTOR_KEYS.indexOf(a);
        const bi = SECTOR_KEYS.indexOf(b);
        if (ai === -1 && bi === -1) return a.localeCompare(b);
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      });

      const cx = chartWidth / 2;
      const cy = chartHeight / 2;
      const padding = 80;
      const clusterRadius = Math.min(chartWidth, chartHeight) / 2 - padding;

      const sectorCenters = {};
      sectorsPresent.forEach(function (s, i) {
        const angle = (i / sectorsPresent.length) * Math.PI * 2 - Math.PI / 2;
        sectorCenters[s] = {
          x: cx + Math.cos(angle) * clusterRadius,
          y: cy + Math.sin(angle) * clusterRadius,
        };
      });

      const sim = d3.forceSimulation(rows)
        .force("charge", d3.forceManyBody().strength(2))
        .force("collide", d3.forceCollide().radius(function (d) { return d.radius + 2; }).iterations(2))
        .force("x", d3.forceX(function (d) { return sectorCenters[d.sector].x; }).strength(0.18))
        .force("y", d3.forceY(function (d) { return sectorCenters[d.sector].y; }).strength(0.18))
        .stop();

      for (let i = 0; i < 300; i++) sim.tick();

      const selectedFill = "#222";

      const bubbleSel = bubbleGroup.selectAll("g.bubble")
        .data(rows, function (d) { return d.ticker; })
        .enter()
        .append("g")
        .attr("class", function (d) { return "bubble " + d.sector.toLowerCase().replace(/\s+/g, "-"); })
        .attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });

      bubbleSel.append("circle")
        .attr("r", function (d) { return d.radius; })
        .attr("fill", function (d) { return d.color; })
        .attr("stroke", "#fff")
        .attr("stroke-width", 1.5)
        .on("mouseover", function (event, d) {
          if (!d.hasTooltip) {
            hideTooltip();
            return;
          }
          tooltip.innerHTML =
            "<div class=\"tt-ticker\">" + ESCAPE_HTML(d.ticker) + "</div>" +
            "<div class=\"tt-name\">" + ESCAPE_HTML(d.name) + "</div>" +
            "<div class=\"tt-sector\">Sector: " + ESCAPE_HTML(d.sector) + "</div>";
          showTooltip();
          positionTooltip(event);
        })
        .on("mousemove", function (event) {
          if (!isTooltipVisible()) return;
          positionTooltip(event);
        })
        .on("mouseout", function () {
          hideTooltip();
        })
        .on("click", function (event, d) {
          event.stopPropagation();
          selectTicker(d.ticker, { source: "bubble" });
        });

      bubbleSel.append("text")
        .attr("text-anchor", "middle")
        .attr("dominant-baseline", "central")
        .text(function (d) { return d.ticker; });

      legendEl.innerHTML = "";
      sectorsPresent.forEach(function (s) {
        const item = document.createElement("span");
        item.className = "legend-item";
        const sw = document.createElement("span");
        sw.className = "legend-swatch";
        sw.style.background = SECTOR_COLORS[s] || "#888";
        const lbl = document.createElement("span");
        lbl.textContent = s;
        item.appendChild(sw);
        item.appendChild(lbl);
        legendEl.appendChild(item);
      });

      const sortedForTable = rows.slice().sort(function (a, b) {
        if (a.sector !== b.sector) return a.sector.localeCompare(b.sector);
        return a.ticker.localeCompare(b.ticker);
      });

      const trSel = tableBody.selectAll("tr")
        .data(sortedForTable, function (d) { return d.ticker; })
        .enter()
        .append("tr")
        .attr("data-ticker", function (d) { return d.ticker; })
        .on("click", function (event, d) {
          selectTicker(d.ticker, { source: "row" });
        });

      trSel.append("td").attr("class", "ticker-cell").text(function (d) { return d.ticker; });

      trSel.append("td").attr("class", "name-cell").text(function (d) { return d.name; });

      trSel.append("td").attr("class", "sector-cell-wrap")
        .html(function (d) {
          return "<span class=\"sector-cell\">" +
            "<span class=\"sector-dot\" style=\"background:" + (SECTOR_COLORS[d.sector] || "#888") + "\"></span>" +
            ESCAPE_HTML(d.sector) +
            "</span>";
        });

      trSel.append("td").attr("class", "mcap-cell").text(function (d) { return FORMAT_MCAP(d.marketCap); });

      let currentSelection = null;

      function selectTicker(ticker, opts) {
        opts = opts || {};
        if (currentSelection === ticker) {
          clearSelection();
          return;
        }
        currentSelection = ticker;
        bubbleGroup.selectAll("g.bubble")
          .classed("selected", function (d) { return d.ticker === ticker; });
        tableBody.selectAll("tr")
          .classed("selected", function (d) { return d.ticker === ticker; });
        if (opts.source === "bubble") {
          const row = tableBody.select("tr[data-ticker=\"" + ticker + "\"]").node();
          if (row) {
            row.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        }
        if (opts.source === "row") {
          const node = bubbleGroup.select("g.bubble circle").filter(function (d) { return d.ticker === ticker; }).node();
          if (node) {
            node.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        }
      }

      function clearSelection() {
        currentSelection = null;
        bubbleGroup.selectAll("g.bubble").classed("selected", false);
        tableBody.selectAll("tr").classed("selected", false);
      }

      svg.on("click", function () {
        clearSelection();
      });

      window.addEventListener("resize", function () {
        measure();
        svg.attr("width", chartWidth).attr("height", chartHeight)
          .attr("viewBox", [0, 0, chartWidth, chartHeight]);
      });
    }).catch(function (err) {
      console.error("Failed to load stock descriptions:", err);
      chartPanel.innerHTML = "<p style=\"color:#b00\">Failed to load data.</p>";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildVisualization);
  } else {
    buildVisualization();
  }
})();