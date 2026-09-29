(function () {
  "use strict";

  const DATA_URL = "data/stock-descriptions.csv";

  const width = 720;
  const height = 520;

  const ETF_UNIFORM_RADIUS = 14;

  const formatMarketCap = (v) => {
    if (v == null || isNaN(v)) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (abs >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (v / 1e3).toFixed(2) + "K";
    return String(v);
  };

  d3.csv(DATA_URL).then((raw) => {
    const rows = raw.map((d) => {
      const mc = d.marketCap && d.marketCap.trim() !== "" ? +d.marketCap : null;
      return {
        ticker: d.ticker,
        name: d["full name"],
        sector: d.sector,
        marketCap: mc,
        country: d.country,
        website: d.website,
        longBusinessSummary: d.longBusinessSummary,
        isETF: d.sector === "ETF",
      };
    });

    const sectors = Array.from(new Set(rows.map((r) => r.sector)));
    sectors.sort((a, b) => {
      if (a === "ETF") return 1;
      if (b === "ETF") return -1;
      return a.localeCompare(b);
    });

    const sectorColor = d3.scaleOrdinal()
      .domain(sectors)
      .range(d3.schemeCategory10.concat(["#7f5af0", "#2cb1bc"]).slice(0, sectors.length));

    const marketCaps = rows.filter((r) => !r.isETF && r.marketCap != null).map((r) => r.marketCap);
    const rMin = 7;
    const rMax = 32;
    let rScale = d3.scaleSqrt()
      .domain([d3.min(marketCaps), d3.max(marketCaps)])
      .range([rMin, rMax]);
    if (!isFinite(rMin) || !isFinite(rMax) || rMin === rMax) {
      rScale = () => (rMin + rMax) / 2;
    }

    const radiusOf = (d) => (d.isETF ? ETF_UNIFORM_RADIUS : rScale(d.marketCap));

    const clusterCenters = computeClusterCenters(sectors, width, height);
    rows.forEach((d) => {
      d.color = sectorColor(d.sector);
      d.radius = radiusOf(d);
      d.cx = clusterCenters[d.sector].x;
      d.cy = clusterCenters[d.sector].y;
    });

    const svg = d3.select("#chart-container")
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    const defs = svg.append("defs");
    defs.append("filter")
      .attr("id", "soft-shadow")
      .attr("x", "-50%").attr("y", "-50%")
      .attr("width", "200%").attr("height", "200%")
      .html('<feGaussianBlur in="SourceAlpha" stdDeviation="1.4"/><feOffset dx="0" dy="1" result="offsetblur"/><feComponentTransfer><feFuncA type="linear" slope="0.35"/></feComponentTransfer><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>');

    const tooltip = d3.select("#tooltip");

    const sim = d3.forceSimulation(rows)
      .force("x", d3.forceX((d) => d.cx).strength(0.18))
      .force("y", d3.forceY((d) => d.cy).strength(0.18))
      .force("collide", d3.forceCollide((d) => d.radius + 1.5))
      .force("charge", d3.forceManyBody().strength(-6))
      .force("center", d3.forceCenter(width / 2, height / 2).strength(0.04));

    const groups = svg.selectAll("g.bubble-group")
      .data(rows, (d) => d.ticker)
      .enter()
      .append("g")
      .attr("class", "bubble-group")
      .attr("data-ticker", (d) => d.ticker);

    groups.append("circle")
      .attr("class", (d) => "bubble" + (d.isETF ? " is-etf" : ""))
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => d.color)
      .attr("fill-opacity", 0.85)
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 1.5)
      .attr("filter", "url(#soft-shadow)")
      .on("mouseover", function (event, d) {
        if (d.isETF) return;
        showTooltip(event, d);
      })
      .on("mousemove", function (event, d) {
        if (d.isETF) return;
        positionTooltip(event);
      })
      .on("mouseout", function () {
        tooltip.classed("visible", false);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        selectTicker(d.ticker);
      });

    groups.append("text")
      .attr("class", "bubble-label")
      .attr("dy", "0.35em")
      .attr("pointer-events", "all")
      .style("font-size", (d) => {
        const long = d.ticker.length >= 4;
        const minSize = long ? 7 : 9;
        const maxSize = long ? 12 : 14;
        const size = Math.max(minSize, Math.min(maxSize, d.radius * 0.36));
        return size + "px";
      })
      .text((d) => d.ticker)
      .on("mouseover", function (event, d) {
        if (d.isETF) return;
        showTooltip(event, d);
      })
      .on("mousemove", function (event, d) {
        if (d.isETF) return;
        positionTooltip(event);
      })
      .on("mouseout", function (event) {
        const g = this.parentNode;
        const related = event.relatedTarget;
        if (related && g.contains(related)) return;
        tooltip.classed("visible", false);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        selectTicker(d.ticker);
      });

    function showTooltip(event, d) {
      tooltip
        .classed("visible", true)
        .html(
          `<div class="ticker">${escapeHTML(d.ticker)}</div>` +
          `<div class="name">${escapeHTML(d.name)}</div>` +
          `<div class="sector-label">${escapeHTML(d.sector)}</div>`
        );
      positionTooltip(event);
    }

    function positionTooltip(event) {
      const containerRect = document.getElementById("chart-container").getBoundingClientRect();
      const x = event.clientX - containerRect.left;
      const y = event.clientY - containerRect.top;
      tooltip.style("left", x + "px").style("top", y + "px");
    }

    sim.on("tick", () => {
      groups.attr("transform", (d) => {
        const x = Math.max(d.radius, Math.min(width - d.radius, d.x));
        const y = Math.max(d.radius, Math.min(height - d.radius, d.y));
        return `translate(${x},${y})`;
      });
    });

    drawLegend(sectors, sectorColor);

    const sortKey = { key: "marketCap", dir: "desc" };
    let currentFilter = "All";

    function renderTable() {
      let view = rows.slice();
      if (currentFilter !== "All") view = view.filter((r) => r.sector === currentFilter);
      const q = (document.getElementById("search-input").value || "").trim().toLowerCase();
      if (q) {
        view = view.filter((r) =>
          r.ticker.toLowerCase().includes(q) ||
          r.name.toLowerCase().includes(q)
        );
      }
      view.sort(comparator(sortKey));
      const tbody = d3.select("#stock-table tbody");
      const tr = tbody.selectAll("tr").data(view, (d) => d.ticker);
      const trEnter = tr.enter().append("tr").attr("data-ticker", (d) => d.ticker);
      trEnter.append("td").attr("class", "cell-ticker");
      trEnter.append("td").attr("class", "cell-name");
      trEnter.append("td").attr("class", "cell-sector");
      trEnter.append("td").attr("class", "cell-cap num");
      const merged = trEnter.merge(tr);
      merged.select(".cell-ticker").text((d) => d.ticker);
      merged.select(".cell-name").text((d) => d.name);
      merged.select(".cell-sector").html((d) =>
        `<span class="sector-dot" style="background:${d.color}"></span>${escapeHTML(d.sector)}`
      );
      merged.select(".cell-cap").text((d) => formatMarketCap(d.marketCap));
      merged
        .on("click", function (event, d) {
          event.stopPropagation();
          selectTicker(d.ticker);
        })
        .classed("selected", (d) => d.ticker === state.selectedTicker);
      tr.exit().remove();
    }

    function comparator(sortKey) {
      const { key, dir } = sortKey;
      const mul = dir === "asc" ? 1 : -1;
      return (a, b) => {
        let av = a[key];
        let bv = b[key];
        if (key === "marketCap") {
          av = av == null ? -Infinity : av;
          bv = bv == null ? -Infinity : bv;
        }
        if (typeof av === "string") av = av.toLowerCase();
        if (typeof bv === "string") bv = bv.toLowerCase();
        if (av < bv) return -1 * mul;
        if (av > bv) return 1 * mul;
        return 0;
      };
    }

    const state = { selectedTicker: null };

    function selectTicker(ticker) {
      state.selectedTicker = ticker;
      groups.select("circle.bubble").classed("selected", (d) => d.ticker === ticker);
      d3.selectAll("#stock-table tbody tr").classed("selected", function () {
        return this.getAttribute("data-ticker") === ticker;
      });
    }

    d3.selectAll("#stock-table thead th").on("click", function () {
      const key = this.getAttribute("data-sort");
      if (!key) return;
      if (sortKey.key === key) sortKey.dir = sortKey.dir === "asc" ? "desc" : "asc";
      else {
        sortKey.key = key;
        sortKey.dir = key === "marketCap" ? "desc" : "asc";
      }
      d3.selectAll("#stock-table thead th").classed("sort-asc", false).classed("sort-desc", false);
      d3.select(this).classed(sortKey.dir === "asc" ? "sort-asc" : "sort-desc", true);
      renderTable();
    });

    const filterSelect = document.getElementById("filter-select");
    sectors.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      filterSelect.appendChild(opt);
    });
    filterSelect.addEventListener("change", (e) => {
      currentFilter = e.target.value;
      renderTable();
    });
    document.getElementById("search-input").addEventListener("input", () => renderTable());

    svg.on("click", () => {
      state.selectedTicker = null;
      groups.select("circle.bubble").classed("selected", false);
      d3.selectAll("#stock-table tbody tr").classed("selected", false);
    });

    renderTable();

    function drawLegend(sectors, colorScale) {
      const legend = d3.select("#legend");
      legend.selectAll(".legend-item").remove();
      const items = legend.selectAll(".legend-item")
        .data(sectors)
        .enter()
        .append("div")
        .attr("class", "legend-item");
      items.append("span")
        .attr("class", "legend-swatch")
        .style("background", (d) => colorScale(d));
      items.append("span").text((d) => d);
    }

    function computeClusterCenters(sectors, w, h) {
      const n = sectors.length;
      const cols = Math.ceil(Math.sqrt(n));
      const rows = Math.ceil(n / cols);
      const padX = 130;
      const padY = 110;
      const stepX = (w - 2 * padX) / Math.max(1, cols - 1);
      const stepY = (h - 2 * padY) / Math.max(1, rows - 1);
      const centers = {};
      sectors.forEach((s, i) => {
        const c = i % cols;
        const r = Math.floor(i / cols);
        centers[s] = {
          x: cols === 1 ? w / 2 : padX + c * stepX,
          y: rows === 1 ? h / 2 : padY + r * stepY,
        };
      });
      return centers;
    }

    function escapeHTML(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }
  }).catch((err) => {
    console.error("Failed to load data:", err);
    d3.select("#chart-container").append("div")
      .style("padding", "20px")
      .style("color", "#b00020")
      .text("Failed to load data/stock-descriptions.csv: " + err.message);
  });
})();
