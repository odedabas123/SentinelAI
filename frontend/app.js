// ==========================================
// SENTINELAI API URLS
// ==========================================

const SENTINEL_API_BASE_URL =
    window.SENTINEL_API_URL ||
    `${window.location.protocol}//${window.location.hostname}:8002`;

const ANOMALIES_API_URL =
    `${SENTINEL_API_BASE_URL}/api/anomalies`;

const SERVICES_API_URL =
    `${SENTINEL_API_BASE_URL}/api/services`;

const METRICS_API_URL =
    `${SENTINEL_API_BASE_URL}/api/metrics`;


// ==========================================
// HTML ELEMENTS
// ==========================================

const serviceCountElement =
    document.getElementById("service-count");

const incidentCountElement =
    document.getElementById("incident-count");

const systemStatusElement =
    document.getElementById("system-status");

const servicesContainer =
    document.getElementById("services-container");

const incidentsContainer =
    document.getElementById("incidents-container");

const incidentStatusFilter =
    document.getElementById("incident-status-filter");

const incidentSeverityFilter =
    document.getElementById("incident-severity-filter");

const incidentServiceFilter =
    document.getElementById("incident-service-filter");

const incidentTypeFilter =
    document.getElementById("incident-type-filter");

const latestLatencyElement =
    document.getElementById("latest-latency");

setupIncidentFilters();

const incidentFilters = {
    status: "all",
    severity: "all",
    service: "all",
    type: "all",
};

function normalizeIncidentStatus(incident) {
    if (incident.status === "ACTIVE") {
        return "ACTIVE";
    }

    if (incident.status === "RESOLVED") {
        return "RESOLVED";
    }

    return "LEGACY";
}

function getIncidentSeverity(incident) {
    return (
        incident.severity ||
        (
            Number(
                incident.status_code ?? incident.status ?? 0
            ) >= 500
                ? "CRITICAL"
                : "HIGH"
        )
    );
}

function getIncidentType(incident) {
    return (
        incident.incident_type ||
        (
            Number(
                incident.status_code ?? incident.status ?? 0
            ) >= 500
                ? "HTTP_ERROR"
                : "LATENCY_ANOMALY"
        )
    );
}

function populateIncidentFilterOptions(incidents) {
    const severityValues = [
        ...new Set(
            incidents.map(
                (incident) =>
                    getIncidentSeverity(incident)
            )
        ),
    ].sort();

    const serviceValues = [
        ...new Set(
            incidents.map(
                (incident) =>
                    incident.service
            )
        ),
    ].filter(Boolean).sort();

    const typeValues = [
        ...new Set(
            incidents.map(
                (incident) =>
                    getIncidentType(incident)
            )
        ),
    ].sort();

    const fillSelect = (
        select,
        values,
        labelKey,
        currentValue,
    ) => {
        const options = [
            "<option value='all'>All</option>",
            ...values.map(
                (value) =>
                    `<option value="${value}">${value}</option>`
            ),
        ].join("");

        select.innerHTML = options;
        select.value = currentValue;
    };

    fillSelect(
        incidentSeverityFilter,
        severityValues,
        "Severity",
        incidentFilters.severity,
    );

    fillSelect(
        incidentServiceFilter,
        serviceValues,
        "Service",
        incidentFilters.service,
    );

    fillSelect(
        incidentTypeFilter,
        typeValues,
        "Type",
        incidentFilters.type,
    );
}

function applyIncidentFilters(incidents) {
    const filteredIncidents = incidents.filter(
        (incident) => {
            const statusMatch =
                incidentFilters.status === "all"
                    || normalizeIncidentStatus(incident) === incidentFilters.status;

            const severityMatch =
                incidentFilters.severity === "all"
                    || getIncidentSeverity(incident) === incidentFilters.severity;

            const serviceMatch =
                incidentFilters.service === "all"
                    || incident.service === incidentFilters.service;

            const typeMatch =
                incidentFilters.type === "all"
                    || getIncidentType(incident) === incidentFilters.type;

            return (
                statusMatch
                && severityMatch
                && serviceMatch
                && typeMatch
            );
        }
    );

    renderIncidents(filteredIncidents);
}

function setupIncidentFilters() {
    [
        incidentStatusFilter,
        incidentSeverityFilter,
        incidentServiceFilter,
        incidentTypeFilter,
    ].forEach((filterElement) => {
        if (!filterElement) {
            return;
        }

        filterElement.addEventListener(
            "change",
            () => {
                const incidentData =
                    window.__sentinelIncidents || [];

                incidentFilters.status =
                    incidentStatusFilter.value;
                incidentFilters.severity =
                    incidentSeverityFilter.value;
                incidentFilters.service =
                    incidentServiceFilter.value;
                incidentFilters.type =
                    incidentTypeFilter.value;

                applyIncidentFilters(incidentData);
            }
        );
    });
}


// ==========================================
// CREATE LATENCY GRAPH
// ==========================================

// Get the canvas from index.html
const latencyCanvas =
    document.getElementById("latency-chart");


// Create the Chart.js graph once.
//
// After this, we only replace its data
// whenever new metrics arrive.
const latencyChart =
    new Chart(
        latencyCanvas,
        {
            type: "line",

            data: {
                labels: [],

                datasets: [
                    {
                        label: "Latency (ms)",

                        data: [],

                        borderWidth: 2,

                        pointRadius: 3,

                        tension: 0.25,
                    }
                ]
            },

            options: {

                responsive: true,

                maintainAspectRatio: false,

                interaction: {
                    mode: "index",
                    intersect: false,
                },

                plugins: {
                    legend: {
                        labels: {
                            color: "#94a3b8"
                        }
                    }
                },

                scales: {

                    x: {
                        ticks: {
                            color: "#64748b",
                            maxTicksLimit: 8,
                        },

                        grid: {
                            color:
                                "rgba(148, 163, 184, 0.08)"
                        }
                    },

                    y: {
                        beginAtZero: true,

                        ticks: {
                            color: "#64748b",

                            callback: function(value) {
                                return value + " ms";
                            }
                        },

                        grid: {
                            color:
                                "rgba(148, 163, 184, 0.08)"
                        }
                    }
                }
            }
        }
    );


// ==========================================
// LOAD LATENCY METRICS
// ==========================================

async function loadMetrics() {

    try {

        // Ask SentinelAI API for recent metrics
        const response = await fetch(
            METRICS_API_URL
        );


        if (!response.ok) {
            throw new Error(
                "Could not load metrics"
            );
        }


        const data =
            await response.json();

        const metrics =
            Array.isArray(data.metrics)
                ? data.metrics
                : [];


        // If there are no requests yet,
        // there is nothing to graph.
        if (metrics.length === 0) {

            latestLatencyElement.textContent =
                "--";

            return;
        }


        // Only show the latest 30 requests
        // so the graph remains easy to read.
        const recentMetrics =
            metrics.slice(-30);


        // Create labels for the horizontal axis
        // using request timestamps.
        const labels =
            recentMetrics.map(
                (metric) => {

                    const date =
                        new Date(
                            metric.timestamp
                        );

                    return date.toLocaleTimeString(
                        [],
                        {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                        }
                    );
                }
            );


        // Extract latency values
        // for the vertical axis.
        const latencyValues =
            recentMetrics.map(
                (metric) =>
                    metric.latency_ms
            );


        // Replace graph data
        latencyChart.data.labels =
            labels;

        latencyChart.data.datasets[0].data =
            latencyValues;


        // Redraw the graph
        latencyChart.update();


        // Get the newest metric
        const latestMetric =
            recentMetrics[
                recentMetrics.length - 1
            ];


        // Show the latest latency
        // above the graph.
        latestLatencyElement.textContent =
            `${latestMetric.latency_ms} ms`;

    } catch (error) {

        console.error(
            "Metrics loading error:",
            error
        );
    }
}


// ==========================================
// LOAD SERVICE HEALTH
// ==========================================

async function loadServices() {

    try {

        const response = await fetch(
            SERVICES_API_URL
        );


        if (!response.ok) {
            throw new Error(
                "Could not load service health"
            );
        }


        const data =
            await response.json();


        serviceCountElement.textContent =
            data.count;


        renderServices(
            data.services
        );


        updateSystemStatus(
            data.services
        );

    } catch (error) {

        servicesContainer.innerHTML = `
            <div class="empty-state">
                <p>
                    Could not load service health.
                </p>
            </div>
        `;


        systemStatusElement.textContent =
            "UNKNOWN";


        console.error(
            "Service health error:",
            error
        );
    }
}


// ==========================================
// DISPLAY SERVICES
// ==========================================

function renderServices(services) {

    servicesContainer.innerHTML = "";


    services.forEach((service) => {

        const card =
            document.createElement("div");


        let serviceClass =
            "service-card";


        if (service.status === "HEALTHY") {

            serviceClass +=
                " service-healthy";

        } else if (service.status === "SLOW") {

            serviceClass +=
                " service-slow";

        } else {

            serviceClass +=
                " service-down";
        }


        card.className =
            serviceClass;


        card.innerHTML = `

            <div class="service-header">

                <div class="service-name">
                    ${service.name}
                </div>

                <div class="service-status">

                    <span class="status-dot"></span>

                    <span>
                        ${service.status}
                    </span>

                </div>

            </div>
        `;


        servicesContainer.appendChild(
            card
        );
    });
}


// ==========================================
// OVERALL SYSTEM STATUS
// ==========================================

function updateSystemStatus(services) {

    const hasDanger =
        services.some(
            (service) =>
                service.status === "DOWN" ||
                service.status === "FAILING"
        );


    const hasSlowService =
        services.some(
            (service) =>
                service.status === "SLOW"
        );


    systemStatusElement.classList.remove(
        "status-healthy",
        "status-warning",
        "status-danger"
    );


    if (hasDanger) {

        systemStatusElement.textContent =
            "DEGRADED";

        systemStatusElement.classList.add(
            "status-danger"
        );

        return;
    }


    if (hasSlowService) {

        systemStatusElement.textContent =
            "DEGRADED";

        systemStatusElement.classList.add(
            "status-warning"
        );

        return;
    }


    systemStatusElement.textContent =
        "HEALTHY";


    systemStatusElement.classList.add(
        "status-healthy"
    );
}


// ==========================================
// LOAD INCIDENTS
// ==========================================

async function loadIncidents() {

    try {

        const response = await fetch(
            ANOMALIES_API_URL
        );


        if (!response.ok) {
            throw new Error(
                "Could not load incidents"
            );
        }


        const data =
            await response.json();

        const incidents =
            data.incidents ?? [];


        incidentCountElement.textContent =
            data.count ?? incidents.length;

        window.__sentinelIncidents = incidents;

        populateIncidentFilterOptions(incidents);

        incidentFilters.status =
            incidentStatusFilter.value || "all";
        incidentFilters.severity =
            incidentSeverityFilter.value || "all";
        incidentFilters.service =
            incidentServiceFilter.value || "all";
        incidentFilters.type =
            incidentTypeFilter.value || "all";

        applyIncidentFilters(incidents);

    } catch (error) {

        incidentsContainer.innerHTML = `
            <div class="empty-state">
                <p>
                    Could not connect to SentinelAI API.
                </p>
            </div>
        `;


        console.error(
            "Incident loading error:",
            error
        );
    }
}


// ==========================================
// DISPLAY INCIDENTS
// ==========================================

function renderIncidents(incidents) {

    const safeIncidents =
        Array.isArray(incidents)
            ? incidents
            : [];


    if (safeIncidents.length === 0) {

        incidentsContainer.innerHTML = `
            <div class="empty-state">
                <p>
                    No incidents detected.
                </p>
            </div>
        `;

        return;
    }


    const activeIncidents =
        safeIncidents.filter(
            (incident) =>
                normalizeIncidentStatus(incident) === "ACTIVE"
        );


    const historicalIncidents =
        safeIncidents.filter(
            (incident) =>
                normalizeIncidentStatus(incident) !== "ACTIVE"
        );


    const renderIncidentGroup = (
        title,
        items,
        emptyMessage
    ) => {
        if (items.length === 0) {
            return `
                <div class="incident-group">
                    <h3 class="incident-group-title">
                        ${title}
                    </h3>
                    <div class="empty-state small-empty-state">
                        <p>
                            ${emptyMessage}
                        </p>
                    </div>
                </div>
            `;
        }


        const cards = items
            .map((incident) => {
                const lifecycleStatus =
                    incident.status || "LEGACY";

                const incidentStatusCode =
                    incident.status_code ??
                    incident.status ??
                    "—";

                const incidentType =
                    incident.incident_type ||
                    (
                        Number(incident.status_code ?? incident.status ?? 0)
                            >= 500
                            ? "HTTP_ERROR"
                            : "LATENCY_ANOMALY"
                    );

                const severity =
                    incident.severity ||
                    (
                        Number(incident.status_code ?? incident.status ?? 0)
                            >= 500
                            ? "CRITICAL"
                            : "HIGH"
                    );

                const formatTimestamp = (value) => {
                    if (!value) {
                        return "—";
                    }

                    const parsed =
                        new Date(value);

                    if (Number.isNaN(parsed.getTime())) {
                        return "—";
                    }

                    return parsed.toLocaleString();
                };

                const formatDuration = (
                    startValue,
                    endValue
                ) => {
                    if (!startValue || !endValue) {
                        return "—";
                    }

                    const start =
                        new Date(startValue).getTime();

                    const end =
                        new Date(endValue).getTime();

                    if (Number.isNaN(start) || Number.isNaN(end)) {
                        return "—";
                    }

                    const diffMs =
                        Math.max(0, end - start);

                    const totalSeconds =
                        Math.floor(diffMs / 1000);

                    const minutes =
                        Math.floor(totalSeconds / 60);

                    const seconds =
                        totalSeconds % 60;

                    return `${minutes}m ${seconds}s`;
                };

                const startedAt =
                    incident.started_at ||
                    incident.timestamp ||
                    "—";

                const resolvedAt =
                    incident.resolved_at ||
                    "—";

                const lastSeenAt =
                    incident.last_seen_at ||
                    startedAt;

                const durationText =
                    formatDuration(
                        incident.started_at,
                        incident.resolved_at
                    );

                let incidentTitle;


                if (incidentType === "HTTP_ERROR") {

                    incidentTitle =
                        "HTTP Error";

                } else if (
                    incidentType === "LATENCY_ANOMALY"
                ) {

                    incidentTitle =
                        "Latency Anomaly";

                } else {

                    incidentTitle =
                        incidentType;
                }


                const severityClass =
                    severity === "CRITICAL"
                        ? "severity-critical"
                        : severity === "HIGH"
                            ? "severity-high"
                            : "severity-medium";

                const lifecycleClass =
                    lifecycleStatus === "ACTIVE"
                        ? "lifecycle-active"
                        : lifecycleStatus === "RESOLVED"
                            ? "lifecycle-resolved"
                            : "lifecycle-legacy";

                const lifecycleLabel =
                    lifecycleStatus === "ACTIVE"
                        ? "ACTIVE"
                        : lifecycleStatus === "RESOLVED"
                            ? "RESOLVED"
                            : "LEGACY";

                const occurrenceCount =
                    incident.occurrence_count ??
                    1;

                return `
                    <div class="incident-card">
                        <div class="incident-header">
                            <div>
                                <div class="incident-service">
                                    ${incident.service || "Unknown service"}
                                </div>
                                <div class="incident-type">
                                    ${incidentTitle}
                                </div>
                            </div>

                            <div class="incident-badges">
                                <div class="incident-badge ${severityClass}">
                                    ${severity}
                                </div>
                                <div class="incident-badge ${lifecycleClass}">
                                    ${lifecycleLabel}
                                </div>
                            </div>
                        </div>

                        <div class="incident-details">
                            <div class="detail-item">
                                <span class="detail-label">
                                    Endpoint
                                </span>
                                <span class="detail-value">
                                    ${incident.method || "-"} ${incident.path || "-"}
                                </span>
                            </div>

                            <div class="detail-item">
                                <span class="detail-label">
                                    Latency
                                </span>
                                <span class="detail-value latency-value">
                                    ${incident.latency_ms ?? "-"} ${incident.latency_ms !== undefined ? "ms" : ""}
                                </span>
                            </div>

                            <div class="detail-item">
                                <span class="detail-label">
                                    Status Code
                                </span>
                                <span class="detail-value">
                                    ${incidentStatusCode}
                                </span>
                            </div>

                            <div class="detail-item">
                                <span class="detail-label">
                                    ${lifecycleStatus === "ACTIVE"
                                        ? "Started At"
                                        : lifecycleStatus === "RESOLVED"
                                            ? "Resolved At"
                                            : "Detected At"}
                                </span>
                                <span class="detail-value">
                                    ${formatTimestamp(
                                        lifecycleStatus === "RESOLVED"
                                            ? resolvedAt
                                            : startedAt
                                    )}
                                </span>
                            </div>

                            <div class="detail-item">
                                <span class="detail-label">
                                    Occurrences
                                </span>
                                <span class="detail-value">
                                    ${occurrenceCount}
                                </span>
                            </div>

                            <div class="detail-item">
                                <span class="detail-label">
                                    Last Seen
                                </span>
                                <span class="detail-value">
                                    ${formatTimestamp(lastSeenAt)}
                                </span>
                            </div>

                            ${
                                lifecycleStatus === "RESOLVED"
                                    ? `
                                        <div class="detail-item">
                                            <span class="detail-label">
                                                Duration
                                            </span>
                                            <span class="detail-value">
                                                ${durationText}
                                            </span>
                                        </div>
                                    `
                                    : ""
                            }
                        </div>
                    </div>
                `;
            })
            .join("");

        return `
            <div class="incident-group">
                <h3 class="incident-group-title">
                    ${title}
                </h3>
                ${cards}
            </div>
        `;
    };


    incidentsContainer.innerHTML = `
        ${renderIncidentGroup(
            "Active Incidents",
            activeIncidents,
            "No active incidents.",
        )}
        ${renderIncidentGroup(
            "Incident History",
            historicalIncidents,
            "No incident history yet.",
        )}
    `;
}


// ==========================================
// START DASHBOARD
// ==========================================

// Load everything immediately.
loadServices();
loadIncidents();
loadMetrics();


// Refresh service health every 3 seconds.
setInterval(
    loadServices,
    3000
);


// Refresh latency graph every 3 seconds.
setInterval(
    loadMetrics,
    3000
);


// Refresh incidents every 5 seconds.
setInterval(
    loadIncidents,
    5000
);
