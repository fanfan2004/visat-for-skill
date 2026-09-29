(function () {
    'use strict';

    const CSV_PATH = 'data/stock-descriptions.csv';
    const SECTORS = [
        'Information Technology',
        'Industry',
        'Energy',
        'Financial',
        'ETF'
    ];

    const SECTOR_COLORS = {
        'Information Technology': '#2563eb',
        'Industry': '#dc2626',
        'Energy': '#f59e0b',
        'Financial': '#16a34a',
        'ETF': '#7c3aed'
    };

    function formatMarketCap(value) {
        const cap = Number(value);
        if (!isFinite(cap) || cap <= 0) return '—';
        const trillion = 1e12;
        const billion = 1e9;
        const million = 1e6;
        if (cap >= trillion) {
            return (cap / trillion).toFixed(2).replace(/\.?0+$/, '') + 'T';
        }
        if (cap >= billion) {
            return (cap / billion).toFixed(2).replace(/\.?0+$/, '') + 'B';
        }
        if (cap >= million) {
            return (cap / million).toFixed(2).replace(/\.?0+$/, '') + 'M';
        }
        return String(Math.round(cap));
    }

    function isLightColor(hex) {
        const c = hex.replace('#', '');
        const r = parseInt(c.substr(0, 2), 16);
        const g = parseInt(c.substr(2, 2), 16);
        const b = parseInt(c.substr(4, 2), 16);
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance > 0.6;
    }

    function parseRow(row) {
        const ticker = (row.ticker || '').trim();
        const sector = (row.sector || '').trim();
        const fullName = (row['full name'] || '').trim();
        const marketCapRaw = row.marketCap;
        const marketCapNum = marketCapRaw === '' || marketCapRaw == null
            ? null
            : Number(marketCapRaw);
        const fullTimeEmployees = row.fullTimeEmployees;
        const country = (row.country || '').trim();
        const website = (row.website || '').trim();
        const longBusinessSummary = (row.longBusinessSummary || '').trim();
        return {
            ticker,
            sector,
            fullName,
            marketCapRaw,
            marketCapNum,
            marketCapDisplay: formatMarketCap(marketCapNum),
            isETF: !marketCapNum,
            fullTimeEmployees,
            country,
            website,
            longBusinessSummary
        };
    }

    function buildLegend(container, items) {
        container.selectAll('.legend-item').remove();
        const legend = container.selectAll('.legend-item')
            .data(items)
            .enter()
            .append('div')
            .attr('class', 'legend-item');
        legend.append('span')
            .attr('class', 'legend-swatch')
            .style('background-color', d => d.color);
        legend.append('span')
            .attr('class', 'legend-text')
            .text(d => d.label);
    }

    let selectedTicker = null;

    function setSelected(ticker) {
        selectedTicker = ticker;
        d3.select('#bubble-chart').selectAll('.bubble')
            .classed('selected', d => d.ticker === selectedTicker)
            .classed('dim', d => selectedTicker != null && d.ticker !== selectedTicker);
        d3.select('#stock-table').selectAll('tbody tr')
            .classed('selected', d => d.ticker === selectedTicker);
        const selectedRow = d3.select('#stock-table').selectAll('tbody tr')
            .filter(d => d.ticker === selectedTicker);
        if (!selectedRow.empty()) {
            const node = selectedRow.node();
            if (node && typeof node.scrollIntoView === 'function') {
                node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
    }

    function attachInteraction(svg, nodes, tooltip) {
        const bubbleSel = svg.selectAll('.bubble');
        const labelSel = svg.selectAll('.bubble-label');

        bubbleSel.on('mouseover', function (event, d) {
            if (d.isETF) {
                return;
            }
            tooltip
                .classed('visible', true)
                .html(buildTooltipHTML(d));
            positionTooltip(event, tooltip);
        })
            .on('mousemove', function (event, d) {
                if (d.isETF) return;
                positionTooltip(event, tooltip);
            })
            .on('mouseout', function () {
                tooltip.classed('visible', false);
            })
            .on('click', function (event, d) {
                event.stopPropagation();
                if (d.isETF) {
                    return;
                }
                setSelected(d.ticker === selectedTicker ? null : d.ticker);
            });

        d3.select('body').on('click.chart-clear', function (event) {
            const target = event.target;
            if (target.closest('#bubble-chart') || target.closest('#stock-table')) {
                return;
            }
            setSelected(null);
        });
    }

    function buildTooltipHTML(d) {
        const lines = [];
        lines.push(`<div class="tt-row"><span class="tt-label">Ticker</span><span class="tt-value">${escapeHtml(d.ticker)}</span></div>`);
        lines.push(`<div class="tt-row"><span class="tt-label">Name</span><span class="tt-value">${escapeHtml(d.fullName)}</span></div>`);
        lines.push(`<div class="tt-row"><span class="tt-label">Sector</span><span class="tt-value">${escapeHtml(d.sector)}</span></div>`);
        return lines.join('');
    }

    function escapeHtml(value) {
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function positionTooltip(event, tooltip) {
        const offset = 14;
        let x = event.clientX + offset;
        let y = event.clientY + offset;
        const node = tooltip.node();
        if (node) {
            const rect = node.getBoundingClientRect();
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            if (x + rect.width > vw - 8) {
                x = event.clientX - rect.width - offset;
            }
            if (y + rect.height > vh - 8) {
                y = event.clientY - rect.height - offset;
            }
            if (x < 8) x = 8;
            if (y < 8) y = 8;
        }
        tooltip
            .style('left', x + 'px')
            .style('top', y + 'px');
    }

    function renderChart(container, stocks) {
        const rect = container.node().getBoundingClientRect();
        const width = Math.max(640, Math.floor(rect.width));
        const height = Math.max(540, Math.floor(rect.height));
        const margin = { top: 40, right: 30, bottom: 30, left: 30 };
        const innerWidth = width - margin.left - margin.right;
        const innerHeight = height - margin.top - margin.bottom;

        container.selectAll('svg').remove();

        const svg = container.append('svg')
            .attr('id', 'bubble-svg')
            .attr('width', width)
            .attr('height', height)
            .attr('viewBox', `0 0 ${width} ${height}`)
            .attr('preserveAspectRatio', 'xMidYMid meet');

        const g = svg.append('g')
            .attr('transform', `translate(${margin.left}, ${margin.top})`);

        const radiusMin = 8;
        const radiusMax = 38;
        const etfRadius = 14;

        const caps = stocks.filter(s => !s.isETF).map(s => s.marketCapNum);
        const minCap = Math.min.apply(null, caps);
        const maxCap = Math.max.apply(null, caps);
        const capScale = d3.scaleSqrt()
            .domain([minCap, maxCap])
            .range([radiusMin, radiusMax]);

        stocks.forEach(s => {
            s.radius = s.isETF ? etfRadius : capScale(s.marketCapNum);
        });

        const positions = {};
        const padX = 130;
        const padY = 130;
        const colYs = [padY, innerHeight / 2 + 30, innerHeight - padY];
        const rowXs = [padX, innerWidth / 2, innerWidth - padX];

        const layouts = [
            { sector: 'Information Technology', x: rowXs[0], y: colYs[0] },
            { sector: 'Industry', x: rowXs[1], y: colYs[0] },
            { sector: 'Energy', x: rowXs[2], y: colYs[0] },
            { sector: 'ETF', x: rowXs[0], y: colYs[2] },
            { sector: 'Financial', x: rowXs[1], y: colYs[2] }
        ];

        layouts.forEach(layout => {
            positions[layout.sector] = {
                x: layout.x,
                y: layout.y
            };
        });

        const nodes = stocks.map(s => Object.assign({}, s, {
            fx: null,
            fy: null,
            x: positions[s.sector].x,
            y: positions[s.sector].y
        }));

        const tooltip = d3.select(container.node().parentNode)
            .selectAll('.tooltip')
            .data([null])
            .join('div')
            .attr('class', 'tooltip');

        const clusterLabels = g.append('g')
            .attr('class', 'cluster-titles')
            .selectAll('text.cluster-title')
            .data(SECTORS)
            .enter()
            .append('text')
            .attr('class', 'cluster-title')
            .attr('x', d => positions[d].x)
            .attr('y', d => positions[d].y - 100)
            .text(d => d);

        const bubbleGroup = g.append('g').attr('class', 'bubbles');
        const labelGroup = g.append('g').attr('class', 'bubble-labels');

        const bubbleSel = bubbleGroup.selectAll('circle.bubble')
            .data(nodes)
            .enter()
            .append('circle')
            .attr('class', d => 'bubble' + (d.isETF ? ' etf' : ''))
            .attr('r', d => d.radius)
            .attr('fill', d => SECTOR_COLORS[d.sector] || '#888')
            .attr('opacity', 0.92);

        const labelSel = labelGroup.selectAll('text.bubble-label')
            .data(nodes)
            .enter()
            .append('text')
            .attr('class', d => 'bubble-label' + (isLightColor(SECTOR_COLORS[d.sector] || '#888') ? ' dark' : ''))
            .text(d => d.ticker);

        const simulation = d3.forceSimulation(nodes)
            .force('x', d3.forceX(d => positions[d.sector].x).strength(0.18))
            .force('y', d3.forceY(d => positions[d.sector].y).strength(0.18))
            .force('charge', d3.forceManyBody().strength(-8))
            .force('collide', d3.forceCollide(d => d.radius + 2.5).strength(0.95))
            .alphaDecay(0.05)
            .alphaMin(0.001);

        function clampToArea(d) {
            d.x = Math.max(d.radius, Math.min(innerWidth - d.radius, d.x));
            d.y = Math.max(d.radius, Math.min(innerHeight - d.radius, d.y));
        }

        nodes.forEach(clampToArea);

        simulation.on('tick', () => {
            nodes.forEach(clampToArea);
            bubbleSel
                .attr('cx', d => d.x)
                .attr('cy', d => d.y);
            labelSel
                .attr('x', d => d.x)
                .attr('y', d => d.y + 1);
        });

        attachInteraction(svg, nodes, tooltip);
    }

    function renderTable(panel, stocks) {
        const sortedStocks = stocks.slice().sort((a, b) => {
            return a.ticker.localeCompare(b.ticker);
        });

        const table = panel.select('#stock-table');
        if (table.empty()) return;

        const thead = table.select('thead');
        if (thead.empty()) {
            const t = table.append('thead');
            t.append('tr')
                .selectAll('th')
                .data(['Ticker symbol', 'Full company name', 'Sector', 'Market cap'])
                .enter()
                .append('th')
                .text(d => d);
            table.append('tbody');
        }

        const tbody = table.select('tbody');
        const rows = tbody.selectAll('tr')
            .data(sortedStocks, d => d.ticker);

        const trEnter = rows.enter()
            .append('tr')
            .attr('data-ticker', d => d.ticker);

        trEnter.append('td').attr('class', 'col-ticker');
        trEnter.append('td').attr('class', 'col-name');
        trEnter.append('td').attr('class', 'col-sector');
        trEnter.append('td').attr('class', 'col-cap');

        const trAll = trEnter.merge(rows);

        trAll.select('.col-ticker').html(d => `<strong>${escapeHtml(d.ticker)}</strong>`);
        trAll.select('.col-name').text(d => d.fullName);
        trAll.select('.col-sector').html(d => {
            const color = SECTOR_COLORS[d.sector] || '#9ca3af';
            return `<span class="sector-pill" style="background-color:${color}22;color:${color};border:1px solid ${color}55;">${escapeHtml(d.sector)}</span>`;
        });
        trAll.select('.col-cap').text(d => d.marketCapDisplay);

        trAll.on('click', function (event, d) {
            event.stopPropagation();
            setSelected(d.ticker === selectedTicker ? null : d.ticker);
        });
    }

    function render(stocks) {
        const chartPanel = d3.select('.chart-panel');
        const tablePanel = d3.select('.table-panel');
        const chartContainer = chartPanel.select('#bubble-chart');
        const legendContainer = chartPanel.select('#legend');

        const legendData = SECTORS.map(sector => ({
            label: sector,
            color: SECTOR_COLORS[sector]
        }));
        buildLegend(legendContainer, legendData);

        renderChart(chartContainer, stocks);
        renderTable(tablePanel, stocks);

        const search = d3.select('#search-input');
        search.on('input', function () {
            const q = this.value.trim().toLowerCase();
            d3.select('#stock-table').selectAll('tbody tr').each(function (d) {
                if (!d) return;
                const match = !q
                    || d.ticker.toLowerCase().indexOf(q) !== -1
                    || d.fullName.toLowerCase().indexOf(q) !== -1
                    || d.sector.toLowerCase().indexOf(q) !== -1;
                d3.select(this).style('display', match ? '' : 'none');
            });
        });
    }

    function init() {
        d3.csv(CSV_PATH).then(raw => {
            const stocks = raw.map(parseRow).filter(s => s.ticker);
            render(stocks);
            window.addEventListener('resize', () => {
                render(stocks);
            });
        }).catch(err => {
            console.error('Failed to load data:', err);
            d3.select('.chart-panel').append('div')
                .attr('class', 'error')
                .style('padding', '20px')
                .style('color', '#dc2626')
                .text('Failed to load stock descriptions data: ' + err.message);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
