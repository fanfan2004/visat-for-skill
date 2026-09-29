(function () {
    "use strict";

    const SECTOR_COLORS = {
        "Information Technology": "#3b82f6",
        "Industry": "#f97316",
        "Energy": "#ef4444",
        "Financial": "#22c55e",
        "ETF": "#6b7280"
    };

    const SECTOR_ORDER = [
        "Information Technology",
        "Industry",
        "Energy",
        "Financial",
        "ETF"
    ];

    const formatMarketCap = (v) => {
        if (v == null || isNaN(v)) return "-";
        if (v >= 1e12) return (v / 1e12).toFixed(2) + "T";
        if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
        if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
        return v.toString();
    };

    const tooltip = d3.select("#tooltip");

    const showTooltip = (event, d) => {
        const html =
            "<strong>" + d.ticker + "</strong>" +
            (d.fullName ? "<div>" + d.fullName + "</div>" : "") +
            "<div>Sector: " + d.sector + "</div>";
        tooltip
            .html(html)
            .style("left", (event.pageX + 12) + "px")
            .style("top", (event.pageY - 8) + "px")
            .classed("visible", true);
    };

    const hideTooltip = () => {
        tooltip.classed("visible", false);
    };

    let selectedTicker = null;

    const setSelection = (ticker) => {
        selectedTicker = ticker;

        d3.selectAll("#bubble-chart .bubble")
            .classed("selected", (d) => d.ticker === ticker)
            .classed("dim", (d) => ticker != null && d.ticker !== ticker);

        d3.selectAll("#bubble-chart .bubble-label")
            .classed("dim", (d) => ticker != null && d.ticker !== ticker);

        d3.selectAll("#stocks tbody tr")
            .classed("selected", (d) => d.ticker === ticker)
            .classed("dim", (d) => ticker != null && d.ticker !== ticker);
    };

    d3.csv("data/stock-descriptions.csv").then((rawRows) => {
        const stocks = rawRows
            .map((r) => ({
                ticker: (r.ticker || "").trim(),
                sector: (r.sector || "").trim(),
                fullName: (r["full name"] || "").trim(),
                marketCap: (r.marketCap === "" || r.marketCap == null)
                    ? null
                    : +r.marketCap,
                isETF: ((r.sector || "").trim() === "ETF"),
                country: (r.country || "").trim(),
                website: (r.website || "").trim()
            }))
            .filter((d) => d.ticker)
            .sort((a, b) => a.ticker.localeCompare(b.ticker));

        const caps = stocks.map((s) => s.marketCap).filter((v) => v != null && !isNaN(v));
        const capExtent = d3.extent(caps);
        const maxCapRadius = 42;
        const minCapRadius = 10;
        const radiusScale = d3.scaleSqrt()
            .domain([capExtent[0], capExtent[1]])
            .range([minCapRadius, maxCapRadius]);

        const etfRadius = 14;

        drawBubbleChart(stocks, radiusScale, etfRadius);
        drawTable(stocks);
        drawLegend();

        document.getElementById("status").textContent =
            "Loaded " + stocks.length + " stocks across " + SECTOR_ORDER.length + " sectors.";
    }).catch((err) => {
        console.error("Failed to load stock data", err);
        document.getElementById("status").textContent =
            "Failed to load data: " + err.message;
    });

    function drawBubbleChart(stocks, radiusScale, etfRadius) {
        const width = 760;
        const height = 640;
        const legendHeight = 70;
        const margin = { top: 20, right: 20, bottom: legendHeight, left: 20 };

        const svg = d3.select("#bubble-chart")
            .attr("viewBox", "0 0 " + width + " " + height)
            .attr("preserveAspectRatio", "xMidYMid meet");

        svg.selectAll("*").remove();

        const sectors = Array.from(new Set(stocks.map((s) => s.sector)))
            .sort((a, b) => {
                const ai = SECTOR_ORDER.indexOf(a);
                const bi = SECTOR_ORDER.indexOf(b);
                if (ai === -1 && bi === -1) return a.localeCompare(b);
                if (ai === -1) return 1;
                if (bi === -1) return -1;
                return ai - bi;
            });

        const innerW = width - margin.left - margin.right;
        const innerH = (height - margin.top - margin.bottom);
        const cx = margin.left + innerW / 2;
        const cy = margin.top + innerH / 2;

        const sectorTargets = {};
        const ringRadius = Math.min(innerW, innerH) * 0.27;
        sectors.forEach((sec, i) => {
            const angle = (i / sectors.length) * Math.PI * 2 - Math.PI / 2;
            sectorTargets[sec] = {
                x: cx + Math.cos(angle) * ringRadius,
                y: cy + Math.sin(angle) * ringRadius
            };
        });

        const nodes = stocks.map((s, i) => {
            const target = sectorTargets[s.sector];
            const baseTheta = ((i * 137.5) % 360) * (Math.PI / 180);
            const baseR = 8 + ((i % 5) * 4);
            return Object.assign({}, s, {
                x: target.x + Math.cos(baseTheta) * baseR,
                y: target.y + Math.sin(baseTheta) * baseR,
                vx: 0,
                vy: 0,
                r: s.isETF ? etfRadius : radiusScale(s.marketCap)
            });
        });

        const simulation = d3.forceSimulation(nodes)
            .force("collide", d3.forceCollide().radius((d) => d.r + 3).strength(0.9).iterations(2))
            .force("x", d3.forceX((d) => sectorTargets[d.sector].x).strength(0.22))
            .force("y", d3.forceY((d) => sectorTargets[d.sector].y).strength(0.22))
            .alpha(1)
            .alphaDecay(0.05)
            .stop();

        const TICK_COUNT = 320;
        for (let t = 0; t < TICK_COUNT; t++) {
            simulation.tick();
        }

        nodes.forEach((d) => {
            d.x = Math.max(margin.left + d.r,
                Math.min(width - margin.right - d.r, d.x));
            d.y = Math.max(margin.top + d.r,
                Math.min(height - margin.bottom - d.r, d.y));
        });

        const g = svg.append("g").attr("class", "bubble-group");

        g.selectAll("circle.bubble")
            .data(nodes, (d) => d.ticker)
            .enter()
            .append("circle")
            .attr("class", "bubble")
            .attr("cx", (d) => d.x)
            .attr("cy", (d) => d.y)
            .attr("r", (d) => d.r)
            .attr("fill", (d) => SECTOR_COLORS[d.sector] || "#999")
            .on("mouseover", function (event, d) {
                if (d.isETF) return;
                showTooltip(event, d);
            })
            .on("mousemove", function (event, d) {
                if (d.isETF) return;
                showTooltip(event, d);
            })
            .on("mouseout", hideTooltip)
            .on("click", function (event, d) {
                event.stopPropagation();
                const next = selectedTicker === d.ticker ? null : d.ticker;
                setSelection(next);
            });

        g.selectAll("text.bubble-label")
            .data(nodes, (d) => d.ticker)
            .enter()
            .append("text")
            .attr("class", "bubble-label")
            .attr("x", (d) => d.x)
            .attr("y", (d) => d.y + 1)
            .attr("font-size", (d) => Math.max(8, Math.min(13, Math.floor(d.r / 2.4))))
            .text((d) => d.ticker);

        svg.on("click", () => setSelection(null));
    }

    function drawTable(stocks) {
        const tbody = d3.select("#stocks tbody");
        const rows = tbody.selectAll("tr")
            .data(stocks, (d) => d.ticker)
            .enter()
            .append("tr")
            .attr("data-ticker", (d) => d.ticker)
            .on("click", function (event, d) {
                event.stopPropagation();
                const next = selectedTicker === d.ticker ? null : d.ticker;
                setSelection(next);
            });

        rows.append("td").attr("class", "col-ticker").text((d) => d.ticker);
        rows.append("td").attr("class", "col-name").text((d) => d.fullName);
        rows.append("td").attr("class", "col-sector")
            .html((d) => {
                const color = SECTOR_COLORS[d.sector] || "#999";
                return '<span class="sector-pill" style="background:' + color + ';">' +
                    d.sector + "</span>";
            });
        rows.append("td").attr("class", "col-mcap").text((d) => formatMarketCap(d.marketCap));
    }

    function drawLegend() {
        const legend = d3.select("#legend");
        legend.selectAll("*").remove();
        SECTOR_ORDER.forEach((sec) => {
            const item = legend.append("div").attr("class", "legend-item");
            item.append("span")
                .attr("class", "legend-swatch")
                .style("background", SECTOR_COLORS[sec] || "#999");
            item.append("span").text(sec);
        });
    }
})();
