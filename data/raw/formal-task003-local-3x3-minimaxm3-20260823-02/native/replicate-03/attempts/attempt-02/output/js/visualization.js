(function () {
    "use strict";

    const DATA_URL = "data/stock-descriptions.csv";

    const SECTOR_COLORS = {
        "Information Technology": "#4c78a8",
        "Industry": "#f58518",
        "Energy": "#e45756",
        "Financial": "#54a24b",
        "ETF": "#b279a2"
    };

    const SECTOR_ORDER = [
        "Information Technology",
        "Industry",
        "Energy",
        "Financial",
        "ETF"
    ];

    const ETF_SIZE = 12;
    const MAX_BUBBLE_RADIUS = 52;
    const MIN_BUBBLE_RADIUS = 8;

    function formatMarketCap(value) {
        if (value === null || value === undefined || value === "" || isNaN(+value)) {
            return "—";
        }
        const n = +value;
        if (n >= 1e12) return (n / 1e12).toFixed(2).replace(/\.?0+$/, "") + "T";
        if (n >= 1e9) return (n / 1e9).toFixed(2).replace(/\.?0+$/, "") + "B";
        if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
        if (n >= 1e3) return (n / 1e3).toFixed(2).replace(/\.?0+$/, "") + "K";
        return String(n);
    }

    const tooltip = d3.select("#tooltip");

    function showTooltip(event, d) {
        if (d.sector === "ETF") {
            tooltip.classed("visible", false);
            return;
        }
        const html =
            '<div class="tip-ticker">' + d.ticker + '</div>' +
            '<div class="tip-row"><strong>' + d.fullName + '</strong></div>' +
            '<div class="tip-row">Sector: ' + d.sector + '</div>';
        tooltip
            .html(html)
            .style("left", (event.pageX + 12) + "px")
            .style("top", (event.pageY - 12) + "px")
            .classed("visible", true);
    }

    function hideTooltip() {
        tooltip.classed("visible", false);
    }

    let stocks = [];
    let selectedTicker = null;

    let radiusScale = null;
    function buildRadiusScale() {
        const caps = stocks
            .filter(d => d.sector !== "ETF" && isFinite(+d.marketCap) && +d.marketCap > 0)
            .map(d => +d.marketCap);
        if (caps.length === 0) return;
        const minCap = Math.min.apply(null, caps);
        const maxCap = Math.max.apply(null, caps);
        radiusScale = d3.scaleSqrt()
            .domain([minCap, maxCap])
            .range([MIN_BUBBLE_RADIUS, MAX_BUBBLE_RADIUS]);
    }

    function radiusFor(d) {
        if (d.sector === "ETF") return ETF_SIZE;
        const cap = +d.marketCap;
        if (!isFinite(cap) || cap <= 0) return ETF_SIZE;
        if (!radiusScale) return ETF_SIZE;
        return radiusScale(cap);
    }

    function buildChart(width, height) {
        const svg = d3.select("#bubble-chart")
            .attr("viewBox", "0 0 " + width + " " + height)
            .attr("preserveAspectRatio", "xMidYMid meet");

        const gBubbles = svg.append("g").attr("class", "bubbles");
        const gLabels = svg.append("g").attr("class", "labels");

        const sectors = Array.from(new Set(stocks.map(d => d.sector)))
            .sort((a, b) => SECTOR_ORDER.indexOf(a) - SECTOR_ORDER.indexOf(b));

        const cx = width / 2;
        const cy = height / 2;
        const clusterRingRadius = Math.min(width, height) * 0.22;
        const clusterCenters = {};
        sectors.forEach((sector, i) => {
            const angle = (i / sectors.length) * 2 * Math.PI - Math.PI / 2;
            clusterCenters[sector] = {
                x: cx + Math.cos(angle) * clusterRingRadius,
                y: cy + Math.sin(angle) * clusterRingRadius
            };
        });

        const sortedStocks = stocks.slice().sort((a, b) => a.ticker.localeCompare(b.ticker));
        sortedStocks.forEach((d, i) => {
            const center = clusterCenters[d.sector];
            const offset = (i % 5) - 2;
            const row = Math.floor(i / 5);
            d.x = center.x + offset * 6;
            d.y = center.y + (row - 1) * 6;
        });

        const simulation = d3.forceSimulation(stocks)
            .force("x", d3.forceX(d => clusterCenters[d.sector].x).strength(0.45))
            .force("y", d3.forceY(d => clusterCenters[d.sector].y).strength(0.45))
            .force("collide", d3.forceCollide(d => radiusFor(d) + 3).iterations(3))
            .force("charge", d3.forceManyBody().strength(2))
            .alphaDecay(0.05)
            .stop();

        for (let i = 0; i < 250; i++) simulation.tick();

        // Clamp positions to chart bounds with padding
        const padding = 24;
        stocks.forEach(d => {
            const r = radiusFor(d);
            d.x = Math.max(padding + r, Math.min(width - padding - r, d.x));
            d.y = Math.max(padding + r, Math.min(height - padding - r, d.y));
        });

        const bubbles = gBubbles.selectAll("circle")
            .data(stocks, d => d.ticker)
            .join("circle")
            .attr("class", "bubble")
            .attr("r", d => radiusFor(d))
            .attr("fill", d => SECTOR_COLORS[d.sector] || "#888")
            .attr("stroke", "#ffffff")
            .attr("stroke-width", 1.5)
            .attr("cx", d => d.x)
            .attr("cy", d => d.y)
            .on("mouseover", function (event, d) {
                showTooltip(event, d);
            })
            .on("mousemove", function (event, d) {
                showTooltip(event, d);
            })
            .on("mouseout", function () {
                hideTooltip();
            })
            .on("click", function (event, d) {
                event.stopPropagation();
                selectTicker(d.ticker, { source: "bubble" });
            });

        const labels = gLabels.selectAll("text")
            .data(stocks, d => d.ticker)
            .join("text")
            .attr("class", "bubble-label")
            .attr("x", d => d.x)
            .attr("y", d => d.y)
            .attr("font-size", d => {
                const r = radiusFor(d);
                return Math.max(8, Math.min(12, Math.floor(r * 0.55)));
            })
            .text(d => d.ticker);

        svg.on("click", function () {
            selectTicker(null, { source: "background" });
        });

        function updateSelection() {
            bubbles.classed("selected", d => d.ticker === selectedTicker);
            bubbles.classed("dim", d => selectedTicker !== null && d.ticker !== selectedTicker);
            labels.classed("selected", d => d.ticker === selectedTicker);
            labels.classed("dim", d => selectedTicker !== null && d.ticker !== selectedTicker);
        }

        return { bubbles, labels, updateSelection };
    }

    function buildLegend(sectors) {
        const legend = d3.select("#legend").html("");
        sectors.forEach(sector => {
            const item = legend.append("div")
                .attr("class", "legend-item")
                .attr("data-sector", sector);
            item.append("div")
                .attr("class", "legend-swatch")
                .style("background-color", SECTOR_COLORS[sector] || "#888");
            item.append("span").text(sector);
            item.on("click", function () {
                const isDisabled = d3.select(this).classed("disabled");
                d3.select(this).classed("disabled", !isDisabled);
                const disabled = Array.from(d3.selectAll(".legend-item.disabled"))
                    .map(el => el.getAttribute("data-sector"));
                applySectorFilter(disabled);
            });
        });
    }

    let chartApi = null;
    function applySectorFilter(disabledSectors) {
        const hasFilter = disabledSectors && disabledSectors.length > 0;
        const matches = sector => !hasFilter || disabledSectors.indexOf(sector) === -1;
        if (chartApi) {
            chartApi.bubbles.classed("dim", d => !matches(d.sector));
            chartApi.labels.classed("dim", d => !matches(d.sector));
        }
        d3.selectAll("#stock-table tbody tr")
            .classed("dim", function () {
                const sector = this.getAttribute("data-sector");
                return !matches(sector);
            });
    }

    function buildTable() {
        const tbody = d3.select("#stock-table tbody");
        const rows = tbody.selectAll("tr")
            .data(stocks, d => d.ticker)
            .join("tr")
            .attr("data-ticker", d => d.ticker)
            .attr("data-sector", d => d.sector);

        rows.on("click", function (event, d) {
            selectTicker(d.ticker, { source: "row" });
        });

        rows.append("td").attr("class", "ticker-cell").text(d => d.ticker);
        rows.append("td").text(d => d.fullName);
        rows.append("td").text(d => d.sector);
        rows.append("td").attr("class", "market-cap-cell").text(d => formatMarketCap(d.marketCap));
    }

    function updateTableSelection() {
        const rows = d3.selectAll("#stock-table tbody tr");
        rows.classed("selected", function () {
            return this.getAttribute("data-ticker") === selectedTicker;
        });
        const selectedRow = rows.filter(function () {
            return this.getAttribute("data-ticker") === selectedTicker;
        });
        if (selectedTicker !== null && !selectedRow.empty() && selectedRow.node()) {
            selectedRow.node().scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
    }

    function selectTicker(ticker, opts) {
        selectedTicker = ticker;
        if (chartApi) chartApi.updateSelection();
        updateTableSelection();
    }

    function init() {
        d3.csv(DATA_URL).then(raw => {
            stocks = raw.map(d => {
                const cap = d.marketCap === "" || d.marketCap === undefined ? null : +d.marketCap;
                return {
                    ticker: d.ticker,
                    sector: d.sector,
                    fullName: d["full name"] || d["full_name"] || d.fullName,
                    marketCap: cap,
                    country: d.country,
                    website: d.website
                };
            }).sort((a, b) => SECTOR_ORDER.indexOf(a.sector) - SECTOR_ORDER.indexOf(b.sector) || a.ticker.localeCompare(b.ticker));

            const sectors = Array.from(new Set(stocks.map(d => d.sector)))
                .sort((a, b) => SECTOR_ORDER.indexOf(a) - SECTOR_ORDER.indexOf(b));

            buildLegend(sectors);
            buildRadiusScale();
            chartApi = buildChart(820, 620);
            buildTable();
        }).catch(err => {
            console.error("Failed to load data:", err);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();