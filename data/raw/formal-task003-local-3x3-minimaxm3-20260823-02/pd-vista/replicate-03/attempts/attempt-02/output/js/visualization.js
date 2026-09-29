(function () {
  "use strict";

  const DATA_URL = "data/stock-descriptions.csv";

  const formatMarketCap = function (value) {
    if (value === null || value === undefined || value === "" || isNaN(value)) return "—";
    const n = +value;
    if (!isFinite(n) || n <= 0) return "—";
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2).replace(/\.00$/, "") + "T";
    if (abs >= 1e9) return (n / 1e9).toFixed(2).replace(/\.00$/, "") + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(2).replace(/\.00$/, "") + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(2).replace(/\.00$/, "") + "K";
    return String(n);
  };

  const rowKey = function (d) { return d.ticker; };

  const tooltip = d3.select("#tooltip");

  function showTooltip(event, d) {
    if (d.sector === "ETF") {
      tooltip.classed("visible", false);
      return;
    }
    const lines = [
      `<span class="ticker">${d.ticker}</span>`,
      `<span class="row">${d.name}</span>`,
      `<span class="row">Sector: ${d.sector}</span>`
    ];
    tooltip.html(lines.join(""))
      .style("left", (event.clientX + window.scrollX) + "px")
      .style("top", (event.clientY + window.scrollY) + "px")
      .classed("visible", true)
      .attr("aria-hidden", "false");
  }

  function moveTooltip(event) {
    tooltip
      .style("left", (event.clientX + window.scrollX) + "px")
      .style("top", (event.clientY + window.scrollY) + "px");
  }

  function hideTooltip() {
    tooltip.classed("visible", false).attr("aria-hidden", "true");
  }

  d3.csv(DATA_URL, d3.autoType).then(function (rawRows) {
    const rows = rawRows.map(function (r) {
      return {
        ticker: r.ticker,
        name: r["full name"],
        sector: r.sector,
        marketCap: (r.marketCap === null || r.marketCap === undefined || r.marketCap === "") ? null : +r.marketCap,
        country: r.country,
        website: r.website,
        isETF: r.sector === "ETF"
      };
    }).filter(function (r) { return r.ticker; });

    const sectors = Array.from(new Set(rows.map(function (r) { return r.sector; })));
    sectors.sort();

    const color = d3.scaleOrdinal()
      .domain(sectors)
      .range(d3.schemeTableau10.concat(d3.schemeSet3).slice(0, sectors.length));

    const radiusFor = function (d) {
      if (d.isETF || !d.marketCap) return 14;
      const mc = +d.marketCap;
      const r = 8 * Math.sqrt(mc / 1e10);
      return Math.max(10, Math.min(48, r));
    };

    const container = document.getElementById("bubble-chart");
    const width = container.clientWidth;
    const height = container.clientHeight;

    const svg = d3.select(container).append("svg")
      .attr("viewBox", [0, 0, width, height])
      .attr("preserveAspectRatio", "xMidYMid meet");

    const defs = svg.append("defs");

    sectors.forEach(function (sector) {
      const grad = defs.append("radialGradient")
        .attr("id", "grad-" + sector.replace(/[^A-Za-z0-9]/g, "_"))
        .attr("cx", "35%").attr("cy", "35%").attr("r", "65%");
      const c = d3.color(color(sector));
      grad.append("stop").attr("offset", "0%").attr("stop-color", c.brighter(0.6).formatHex());
      grad.append("stop").attr("offset", "100%").attr("stop-color", c.darker(0.4).formatHex());
    });

    const cluster = (function () {
      const cols = Math.ceil(Math.sqrt(sectors.length));
      const rows = Math.ceil(sectors.length / cols);
      const map = {};
      sectors.forEach(function (s, i) {
        const cx = ((i % cols) + 0.5) / cols;
        const cy = (Math.floor(i / cols) + 0.5) / rows;
        map[s] = { x: cx * width, y: cy * height };
      });
      return map;
    })();

    const nodes = rows.map(function (r) {
      return {
        ticker: r.ticker,
        name: r.name,
        sector: r.sector,
        marketCap: r.marketCap,
        isETF: r.isETF,
        radius: radiusFor(r),
        x: width / 2 + (Math.random() - 0.5) * width * 0.6,
        y: height / 2 + (Math.random() - 0.5) * height * 0.6
      };
    });

    const simulation = d3.forceSimulation(nodes)
      .force("x", d3.forceX(function (d) { return cluster[d.sector].x; }).strength(0.18))
      .force("y", d3.forceY(function (d) { return cluster[d.sector].y; }).strength(0.18))
      .force("collide", d3.forceCollide(function (d) { return d.radius + 2; }).iterations(3))
      .force("charge", d3.forceManyBody().strength(function (d) { return -d.radius * 1.4; }))
      .alpha(1)
      .alphaDecay(0.03);

    const group = svg.append("g").attr("class", "bubbles");

    const bubble = group.selectAll("circle")
      .data(nodes, rowKey)
      .join("circle")
      .attr("class", "bubble")
      .attr("r", function (d) { return d.radius; })
      .attr("fill", function (d) { return "url(#grad-" + d.sector.replace(/[^A-Za-z0-9]/g, "_") + ")"; })
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5)
      .on("mouseover", function (event, d) {
        showTooltip(event, d);
        highlight(d.ticker, "hover");
      })
      .on("mousemove", function (event, d) {
        if (d.isETF) return;
        moveTooltip(event);
      })
      .on("mouseout", function (event, d) {
        hideTooltip();
        clearHighlight("hover");
      })
      .on("click", function (event, d) {
        selectTicker(d.ticker);
      });

    const labels = group.selectAll("text")
      .data(nodes, rowKey)
      .join("text")
      .attr("class", "bubble-label")
      .text(function (d) { return d.ticker; })
      .attr("font-size", function (d) { return Math.max(8, Math.min(13, d.radius * 0.55)); });

    simulation.on("tick", function () {
      bubble
        .attr("cx", function (d) { return d.x; })
        .attr("cy", function (d) { return d.y; });
      labels
        .attr("x", function (d) { return d.x; })
        .attr("y", function (d) { return d.y; });
    });

    const tbody = d3.select("#stocks-table tbody");
    let selectedTicker = null;

    function renderTable(filterText) {
      const q = (filterText || "").trim().toLowerCase();
      const filtered = rows.filter(function (r) {
        if (!q) return true;
        return r.ticker.toLowerCase().includes(q)
          || r.name.toLowerCase().includes(q)
          || r.sector.toLowerCase().includes(q);
      });
      const tr = tbody.selectAll("tr")
        .data(filtered, rowKey);

      tr.exit().remove();

      const trEnter = tr.enter().append("tr")
        .attr("data-ticker", rowKey)
        .on("mouseenter", function (event, d) { highlight(d.ticker, "hover"); })
        .on("mouseleave", function (event, d) { clearHighlight("hover"); })
        .on("click", function (event, d) { selectTicker(d.ticker); });

      trEnter.append("td").attr("class", "col-ticker");
      trEnter.append("td").attr("class", "col-name");
      trEnter.append("td").attr("class", "col-sector");
      trEnter.append("td").attr("class", "col-marketcap num");

      const merged = trEnter.merge(tr);

      merged.select(".col-ticker").text(function (d) { return d.ticker; });
      merged.select(".col-name").text(function (d) { return d.name; });
      merged.select(".col-sector").text(function (d) { return d.sector; });
      merged.select(".col-marketcap").text(function (d) { return formatMarketCap(d.marketCap); });

      applySelectionStyles();
    }

    renderTable("");

    d3.select("#filter-input").on("input", function () {
      renderTable(this.value);
    });

    function highlight(ticker, source) {
      bubble.classed("dim", function (d) { return d.ticker !== ticker; })
        .classed("selected", function (d) { return d.ticker === ticker; });
      labels.style("opacity", function (d) { return d.ticker === ticker ? 1 : 0.3; });
      tbody.selectAll("tr").each(function () {
        const row = d3.select(this);
        const key = row.attr("data-ticker");
        row.classed("selected", key === ticker);
        row.classed("dim", key !== ticker);
      });
    }

    function clearHighlight(source) {
      if (selectedTicker) {
        applySelectionStyles();
        return;
      }
      bubble.classed("dim", false).classed("selected", false);
      labels.style("opacity", 1);
      tbody.selectAll("tr").classed("selected", false).classed("dim", false);
    }

    function applySelectionStyles() {
      if (selectedTicker) {
        bubble.classed("dim", function (d) { return d.ticker !== selectedTicker; })
          .classed("selected", function (d) { return d.ticker === selectedTicker; });
        labels.style("opacity", function (d) { return d.ticker === selectedTicker ? 1 : 0.3; });
        tbody.selectAll("tr").each(function () {
          const row = d3.select(this);
          const key = row.attr("data-ticker");
          row.classed("selected", key === selectedTicker);
          row.classed("dim", key !== selectedTicker);
        });
      } else {
        bubble.classed("dim", false).classed("selected", false);
        labels.style("opacity", 1);
        tbody.selectAll("tr").classed("selected", false).classed("dim", false);
      }
    }

    function selectTicker(ticker) {
      selectedTicker = (selectedTicker === ticker) ? null : ticker;
      applySelectionStyles();
      if (selectedTicker) {
        const rowNode = tbody.select(`tr[data-ticker="${CSS.escape(selectedTicker)}"]`).node();
        if (rowNode && rowNode.scrollIntoView) {
          rowNode.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }
    }

    const legend = d3.select("#legend");
    const legendItems = legend.selectAll(".legend-item")
      .data(sectors)
      .join("div")
      .attr("class", "legend-item")
      .on("click", function (event, sector) {
        const tickers = rows.filter(function (r) { return r.sector === sector; }).map(function (r) { return r.ticker; });
        if (!tickers.length) return;
        selectedTicker = tickers[Math.floor(Math.random() * tickers.length)];
        applySelectionStyles();
      });

    legendItems.append("span")
      .attr("class", "swatch")
      .style("background", function (d) { return color(d); });

    legendItems.append("span").text(function (d) { return d; });

    window.addEventListener("resize", function () {
      const w = container.clientWidth;
      const h = container.clientHeight;
      svg.attr("viewBox", [0, 0, w, h]);
      const cols = Math.ceil(Math.sqrt(sectors.length));
      const rowsN = Math.ceil(sectors.length / cols);
      sectors.forEach(function (s, i) {
        cluster[s].x = ((i % cols) + 0.5) / cols * w;
        cluster[s].y = (Math.floor(i / cols) + 0.5) / rowsN * h;
      });
      simulation.force("x", d3.forceX(function (d) { return cluster[d.sector].x; }).strength(0.18));
      simulation.force("y", d3.forceY(function (d) { return cluster[d.sector].y; }).strength(0.18));
      simulation.alpha(0.6).restart();
    });
  }).catch(function (err) {
    console.error("Failed to load stock data:", err);
    d3.select("#bubble-chart").append("div")
      .style("color", "#b91c1c")
      .style("padding", "20px")
      .text("Could not load stock data: " + err.message);
  });
})();
