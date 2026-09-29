(function () {
    'use strict';

    // ---------------- Deterministic helpers ----------------
    function seededHash(str) {
        let h = 2166136261 >>> 0;
        for (let i = 0; i < str.length; i++) {
            h ^= str.charCodeAt(i);
            h = Math.imul(h, 16777619);
        }
        return h >>> 0;
    }

    // Deterministic [0, 1) PRNG (mulberry32) so the layout is stable across runs.
    function mulberry32(seed) {
        let s = seed >>> 0;
        return function () {
            s = (s + 0x6d2b79f5) >>> 0;
            let t = s;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    // ---------------- Format helpers ----------------
    function formatMarketCap(value) {
        if (value == null || isNaN(value)) return '—';
        const abs = Math.abs(value);
        if (abs >= 1e12) return (value / 1e12).toFixed(2) + 'T';
        if (abs >= 1e9) return (value / 1e9).toFixed(2) + 'B';
        if (abs >= 1e6) return (value / 1e6).toFixed(2) + 'M';
        if (abs >= 1e3) return (value / 1e3).toFixed(2) + 'K';
        return String(value);
    }

    function parseMarketCap(raw) {
        if (raw == null || raw === '') return null;
        const v = +raw;
        return isNaN(v) ? null : v;
    }

    function tickerKey(t) {
        return (t || '').trim().toUpperCase();
    }

    // ---------------- Sector styling ----------------
    // Distinct, color-blind-friendly-ish palette, deterministic order.
    const SECTOR_COLORS = {
        'Information Technology': '#1f77b4',
        'Financial':              '#d62728',
        'Energy':                 '#2ca02c',
        'Industry':               '#9467bd',
        'ETF':                    '#7f7f7f'
    };
    const FALLBACK_COLOR = '#bcbd22';

    function colorForSector(sector) {
        return SECTOR_COLORS[sector] || FALLBACK_COLOR;
    }

    // ---------------- Layout configuration ----------------
    const BUBBLE_W = 760;
    const BUBBLE_H = 620;
    const BUBBLE_PADDING = 28;

    const ETF_RADIUS = 18;
    const STOCK_RADIUS_MIN = 10;
    const STOCK_RADIUS_MAX = 46;

    function radiusForStock(marketCap) {
        if (marketCap == null) return ETF_RADIUS;
        // log scale on market cap -> radius
        const logMc = Math.log10(Math.max(marketCap, 1));
        // Typical log10 range: ~10 (10B) to ~12.4 (2.6T) for this dataset.
        const lo = 10;
        const hi = 12.5;
        const t = Math.max(0, Math.min(1, (logMc - lo) / (hi - lo)));
        return STOCK_RADIUS_MIN + t * (STOCK_RADIUS_MAX - STOCK_RADIUS_MIN);
    }

    // Cluster centers for the 5 sectors, laid out so the whole thing stays centered.
    // The centers are positioned inside the inner chart area (after padding).
    function clusterCenters(sectors) {
        // Pick a layout based on number of sectors. For 5, a pentagon-ish arrangement.
        const cx = BUBBLE_W / 2;
        const cy = BUBBLE_H / 2;
        const rx = (BUBBLE_W / 2) - 110;
        const ry = (BUBBLE_H / 2) - 110;

        // Sorted sectors for a deterministic arrangement.
        const sorted = sectors.slice().sort();
        const n = sorted.length;
        const centers = {};
        if (n === 1) {
            centers[sorted[0]] = [cx, cy];
        } else if (n === 2) {
            centers[sorted[0]] = [cx - rx * 0.6, cy];
            centers[sorted[1]] = [cx + rx * 0.6, cy];
        } else if (n === 3) {
            centers[sorted[0]] = [cx,             cy - ry * 0.6];
            centers[sorted[1]] = [cx - rx * 0.6,  cy + ry * 0.4];
            centers[sorted[2]] = [cx + rx * 0.6,  cy + ry * 0.4];
        } else if (n === 4) {
            centers[sorted[0]] = [cx - rx * 0.55, cy - ry * 0.55];
            centers[sorted[1]] = [cx + rx * 0.55, cy - ry * 0.55];
            centers[sorted[2]] = [cx - rx * 0.55, cy + ry * 0.55];
            centers[sorted[3]] = [cx + rx * 0.55, cy + ry * 0.55];
        } else {
            // 5+ : regular polygon vertices starting at top, going clockwise.
            for (let i = 0; i < n; i++) {
                const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
                centers[sorted[i]] = [cx + rx * 0.78 * Math.cos(angle),
                                       cy + ry * 0.78 * Math.sin(angle)];
            }
        }
        return centers;
    }

    // ---------------- Main ----------------
    document.addEventListener('DOMContentLoaded', () => {
        const tooltip = document.getElementById('tooltip');
        const chartHost = document.getElementById('bubble-chart');
        const legendHost = document.getElementById('legend');
        const tableBody = document.querySelector('#stocks-table tbody');
        const tableHead = document.querySelector('#stocks-table thead');

        d3.csv('data/stock-descriptions.csv').then(raw => {
            // Tidy up: trim whitespace from string fields.
            const stocks = raw.map(d => ({
                ticker:        tickerKey(d.ticker),
                sector:        (d.sector || '').trim(),
                fullName:      (d['full name'] || '').trim(),
                marketCap:     parseMarketCap(d.marketCap),
                country:       (d.country || '').trim(),
                website:       (d.website || '').trim(),
                isETF:         ((d.sector || '').trim() === 'ETF')
            }));

            // Stable sorts.
            stocks.sort((a, b) => a.ticker.localeCompare(b.ticker));

            const sectors = Array.from(new Set(stocks.map(s => s.sector))).sort();
            const centers = clusterCenters(sectors);

            // Assign deterministic initial positions in a small jitter inside
            // each sector's cluster box so the simulation starts roughly arranged.
            const rand = mulberry32(20240823);
            stocks.forEach((s, i) => {
                const [ccx, ccy] = centers[s.sector] || [BUBBLE_W / 2, BUBBLE_H / 2];
                // Small angular jitter inside a tiny ring around the cluster center.
                const r = (i % 3) * 6 + rand() * 4;
                const a = rand() * Math.PI * 2;
                s.x = ccx + Math.cos(a) * r;
                s.y = ccy + Math.sin(a) * r;
                s.radius = s.isETF ? ETF_RADIUS : radiusForStock(s.marketCap);
            });

            // ---------------- Build SVG ----------------
            const svg = d3.select(chartHost)
                .append('svg')
                .attr('width', BUBBLE_W)
                .attr('height', BUBBLE_H)
                .attr('viewBox', `0 0 ${BUBBLE_W} ${BUBBLE_H}`)
                .attr('xmlns', 'http://www.w3.org/2000/svg');

            const g = svg.append('g').attr('class', 'bubbles');

            // Force simulation with fixed tick count (deterministic).
            const simulation = d3.forceSimulation(stocks)
                .force('charge', d3.forceManyBody().strength(2))
                .force('collide', d3.forceCollide().radius(d => d.radius + 2).iterations(2))
                .force('x', d3.forceX(d => centers[d.sector] ? centers[d.sector][0] : BUBBLE_W / 2).strength(0.18))
                .force('y', d3.forceY(d => centers[d.sector] ? centers[d.sector][1] : BUBBLE_H / 2).strength(0.18))
                .stop();

            // Fixed iteration count so layout is identical on every load.
            const TICK_COUNT = 320;
            for (let i = 0; i < TICK_COUNT; i++) simulation.tick();

            // Clamp inside visible area so labels don't get clipped.
            stocks.forEach(s => {
                s.x = Math.max(BUBBLE_PADDING + s.radius, Math.min(BUBBLE_W - BUBBLE_PADDING - s.radius, s.x));
                s.y = Math.max(BUBBLE_PADDING + s.radius, Math.min(BUBBLE_H - BUBBLE_PADDING - s.radius, s.y));
            });

            // Draw bubbles.
            const node = g.selectAll('g.bubble-node')
                .data(stocks, d => d.ticker)
                .enter()
                .append('g')
                .attr('class', 'bubble-node')
                .attr('data-ticker', d => d.ticker.toLowerCase());

            node.append('circle')
                .attr('class', 'bubble')
                .attr('r', d => d.radius)
                .attr('cx', d => d.x)
                .attr('cy', d => d.y)
                .attr('fill', d => colorForSector(d.sector));

            node.append('text')
                .attr('class', d => 'bubble-label' + (d.isETF ? ' etf-label' : ''))
                .attr('x', d => d.x)
                .attr('y', d => d.y)
                .attr('font-size', d => Math.max(8, Math.min(14, d.radius * 0.55)))
                .text(d => d.ticker);

            // ---------------- Legend ----------------
            sectors.forEach(s => {
                const item = document.createElement('div');
                item.className = 'legend-item';
                item.innerHTML = `<span class="legend-swatch" style="background:${colorForSector(s)}"></span>${s}`;
                legendHost.appendChild(item);
            });

            // ---------------- Tooltip & click ----------------
            function showTooltip(event, d) {
                if (d.isETF) return; // Per spec: no tooltip for ETFs.
                tooltip.innerHTML =
                    `<div class="tt-ticker">${d.ticker}</div>` +
                    `<div class="tt-name">${d.fullName}</div>` +
                    `<div class="tt-sector">Sector: ${d.sector}</div>`;
                tooltip.classList.add('visible');
                // Position near the mouse, clamped inside the viewport.
                const pad = 14;
                const x = event.clientX + pad;
                const y = event.clientY + pad;
                tooltip.style.left = x + 'px';
                tooltip.style.top  = y + 'px';
            }

            function moveTooltip(event) {
                const pad = 14;
                tooltip.style.left = (event.clientX + pad) + 'px';
                tooltip.style.top  = (event.clientY + pad) + 'px';
            }

            function hideTooltip() {
                tooltip.classList.remove('visible');
            }

            node.on('mouseover', (event, d) => showTooltip(event, d))
                .on('mousemove', (event, d) => {
                    if (d.isETF) return;
                    moveTooltip(event);
                })
                .on('mouseout', hideTooltip)
                .on('click', (event, d) => {
                    event.stopPropagation();
                    setSelected(d.ticker);
                });

            // Clicking on empty chart area clears selection.
            svg.on('click', () => setSelected(null));

            // ---------------- Table ----------------
            // Build header row (explicit so columns match the spec exactly).
            tableHead.innerHTML =
                '<tr>' +
                '<th>Ticker symbol</th>' +
                '<th>Full company name</th>' +
                '<th>Sector</th>' +
                '<th style="text-align:right">Market cap</th>' +
                '</tr>';

            // Build body rows. Sort the table by ticker for determinism.
            const sortedForTable = stocks.slice().sort((a, b) => a.ticker.localeCompare(b.ticker));
            const rowByTicker = {};
            sortedForTable.forEach(s => {
                const tr = document.createElement('tr');
                tr.dataset.ticker = s.ticker.toLowerCase();
                tr.innerHTML =
                    `<td><strong>${s.ticker}</strong></td>` +
                    `<td>${s.fullName}</td>` +
                    `<td><span class="sector-dot" style="background:${colorForSector(s.sector)}"></span>${s.sector}</td>` +
                    `<td class="market-cap">${formatMarketCap(s.marketCap)}</td>`;
                tr.addEventListener('click', () => setSelected(s.ticker));
                tableBody.appendChild(tr);
                rowByTicker[s.ticker] = tr;
            });

            // ---------------- Selection sync ----------------
            let selected = null;
            function setSelected(ticker) {
                selected = ticker ? tickerKey(ticker) : null;
                // Bubble styling
                node.selectAll('circle.bubble').classed('selected', d => selected != null && d.ticker === selected);
                // Table row styling
                Object.keys(rowByTicker).forEach(t => {
                    rowByTicker[t].classList.toggle('selected', selected != null && t === selected);
                });
                // Scroll the selected row into view if any.
                if (selected && rowByTicker[selected]) {
                    rowByTicker[selected].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
            }
        }).catch(err => {
            console.error('Failed to load stock data:', err);
            chartHost.textContent = 'Failed to load data/stock-descriptions.csv';
        });
    });
})();
