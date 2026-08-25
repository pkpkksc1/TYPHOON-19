const DATA_URL = "./data/dashboard.json";

const $ = (id) => document.getElementById(id);

function safe(v, fallback = "-") {
  return v === null || v === undefined || v === "" ? fallback : v;
}

function fmt(v, suffix = "") {
  return v === null || v === undefined ? "-" : `${v}${suffix}`;
}

function shortTime(v) {
  if (!v) return "-";

  const s = String(v).replace("T", " ");

  return s.length >= 16
    ? s.slice(5, 16)
    : s;
}

function fullTime(v) {
  if (!v) return "-";

  return String(v)
    .replace("T", " ")
    .slice(0, 16);
}

function riskClass(label) {
  if (label === "높음") {
    return "risk-high";
  }

  if (label === "주의") {
    return "risk-warn";
  }

  return "risk-low";
}

function statusClass(label) {
  const text = String(label || "");

  if (
    text.includes("결항") ||
    text.includes("운항 문제")
  ) {
    return "risk-high";
  }

  if (text.includes("지연")) {
    return "risk-warn";
  }

  return "risk-low";
}


// ==========================================
// 태풍 번호 표시
// 2619 -> 26년 19호
// ==========================================

function typhoonDisplay(number, name) {
  const num = String(number || "");

  let year = "";
  let no = "";

  if (num.length === 4) {
    year = num.slice(0, 2);

    no = String(
      parseInt(
        num.slice(2),
        10
      )
    );
  } else {
    no = num || "-";
  }

  if (year) {
    return `${year}년 ${no}호 ${safe(name, "")}`.trim();
  }

  return `${no}호 ${safe(name, "")}`.trim();
}


// ==========================================
// 지도 좌표 변환
// 위도 5~45N / 경도 100~160E
// ==========================================

function mapXY(lat, lon) {
  const minLat = 5;
  const maxLat = 45;

  const minLon = 100;
  const maxLon = 160;

  const x =
    ((lon - minLon) /
      (maxLon - minLon)) *
    100;

  const y =
    (1 -
      ((lat - minLat) /
        (maxLat - minLat))) *
    100;

  return {
    x: Math.max(
      2,
      Math.min(98, x)
    ),

    y: Math.max(
      3,
      Math.min(97, y)
    )
  };
}


// ==========================================
// 예상시간별 색상
// ==========================================

function trackColor(hour) {
  if (hour === 0) {
    return "#ff5b52";
  }

  if (hour === 24) {
    return "#ff8c1a";
  }

  if (hour === 48) {
    return "#ffc82f";
  }

  if (hour === 72) {
    return "#3dcc6b";
  }

  if (hour === 96) {
    return "#1fd0dc";
  }

  return "#2381ff";
}


function trackLabel(hour) {
  if (hour === 0) {
    return "현재";
  }

  return `${hour}시간 후`;
}


// ==========================================
// 대시보드 로드
// ==========================================

async function loadDashboard() {

  const response = await fetch(
    `${DATA_URL}?t=${Date.now()}`
  );

  if (!response.ok) {
    throw new Error(
      `dashboard.json ${response.status}`
    );
  }

  const data =
    await response.json();


  // ======================================
  // 현재 태풍
  // ======================================

  const typhoon =
    data.typhoon || {};

  const current =
    typhoon.current || {};


  $("typhoonTitle").textContent =
    typhoonDisplay(
      typhoon.number,
      typhoon.name
    );


  $("typhoonBaseTime").textContent =
    `기준 시각: ${
      fullTime(current.time)
    }`;


  if (
    current.lat != null &&
    current.lon != null
  ) {

    $("currentPosition").textContent =
      `${current.lat}°N / ${current.lon}°E`;

  } else {

    $("currentPosition").textContent =
      "-";
  }


  $("pressure").textContent =
    fmt(
      current.pressure_hpa,
      " hPa"
    );


  $("maxWind").textContent =
    fmt(
      current.max_wind_mps,
      " m/s"
    );


  $("moveDir").textContent =
    safe(
      current.movement_direction
    );


  $("moveSpeed").textContent =
    fmt(
      current.movement_speed_kmh,
      " km/h"
    );


  // ======================================
  // JMA ↔ KMA 비교
  // ======================================

  const comparison =
    data.forecast_comparison || {};


  $("compareAvg").textContent =
    fmt(
      comparison.average_difference_km,
      " km"
    );


  $("compareMax").textContent =
    fmt(
      comparison.max_difference_km,
      " km"
    );


  $("compareStatus").textContent =
    `${safe(
      comparison.emoji,
      "⚪"
    )} ${safe(
      comparison.label_ko,
      "비교자료 없음"
    )}`;


  // ======================================
  // 태풍 이동 경로
  // ======================================

  const track = [
    {
      forecast_hour: 0,
      time: current.time,
      lat: current.lat,
      lon: current.lon,
      pressure_hpa:
        current.pressure_hpa,
      max_wind_mps:
        current.max_wind_mps
    },

    ...(
      typhoon.forecast_track || []
    )

  ].filter(
    (point) =>
      point.lat != null &&
      point.lon != null
  );


  const pointsWrap =
    $("trackPoints");

  pointsWrap.innerHTML = "";


  const line =
    $("trackLine");

  const svgPoints = [];


  track.forEach(
    (point) => {

      const xy =
        mapXY(
          Number(point.lat),
          Number(point.lon)
        );


      const dot =
        document.createElement(
          "div"
        );

      dot.className =
        "track-point";


      dot.style.left =
        `${xy.x}%`;

      dot.style.top =
        `${xy.y}%`;

      dot.style.background =
        trackColor(
          point.forecast_hour
        );

      dot.style.color =
        trackColor(
          point.forecast_hour
        );


      pointsWrap.appendChild(
        dot
      );


      const label =
        document.createElement(
          "div"
        );

      label.className =
        "track-point-label";


      label.style.left =
        `${xy.x}%`;

      label.style.top =
        `${xy.y}%`;


      label.textContent =
        trackLabel(
          point.forecast_hour
        );


      pointsWrap.appendChild(
        label
      );


      svgPoints.push(
        `${xy.x * 10},${xy.y * 5.2}`
      );
    }
  );


  line.setAttribute(
    "points",
    svgPoints.join(" ")
  );


  // ======================================
  // 예상 경로 표
  // ======================================

  const tbody =
    $("trackTableBody");

  tbody.innerHTML = "";


  track.forEach(
    (point) => {

      const row =
        document.createElement(
          "tr"
        );


      row.innerHTML = `
        <td
          style="
            font-weight:900;
            color:${
              trackColor(
                point.forecast_hour
              )
            }
          "
        >
          ${
            trackLabel(
              point.forecast_hour
            )
          }
        </td>

        <td>
          ${
            shortTime(
              point.time
            )
          }
        </td>

        <td>
          ${
            safe(point.lat)
          }
          /
          ${
            safe(point.lon)
          }
        </td>

        <td>
          ${
            fmt(
              point.pressure_hpa,
              " hPa"
            )
          }
        </td>

        <td>
          ${
            fmt(
              point.max_wind_mps,
              " m/s"
            )
          }
        </td>
      `;


      tbody.appendChild(
        row
      );
    }
  );


  // ======================================
  // 주요 거점
  // ======================================

  const locations =
    $("locationsGrid");

  locations.innerHTML = "";


  Object.entries(
    data.locations || {}
  ).forEach(
    ([code, item]) => {

      const risk =
        item.risk || {};

      const weather =
        item.current_weather || {};


      const card =
        document.createElement(
          "article"
        );


      card.className =
        "location-card";


      card.innerHTML = `
        <div class="location-top">

          <div>

            <div class="location-name">
              ${
                safe(
                  item.name_ko,
                  code
                )
              }
            </div>

            <div class="location-code">
              ${code}
              ·
              ${
                safe(
                  item.trend_ko
                )
              }
            </div>

          </div>


          <div
            class="
              risk-pill
              ${
                riskClass(
                  risk.label_ko
                )
              }
            "
          >
            ${
              safe(
                risk.emoji
              )
            }

            ${
              safe(
                risk.label_ko
              )
            }
          </div>

        </div>


        <div class="location-main">

          <div class="location-stat">
            <span>
              태풍 최접근
            </span>

            <strong>
              ${
                fmt(
                  item.closest_distance_km,
                  " km"
                )
              }
            </strong>
          </div>


          <div class="location-stat">
            <span>
              현재 강수
            </span>

            <strong>
              ${
                fmt(
                  weather.rain_mm,
                  " mm"
                )
              }
            </strong>
          </div>


          <div class="location-stat">
            <span>
              풍속
            </span>

            <strong>
              ${
                fmt(
                  weather.wind_mps,
                  " m/s"
                )
              }
            </strong>
          </div>


          <div class="location-stat">
            <span>
              돌풍
            </span>

            <strong>
              ${
                fmt(
                  weather.gust_mps,
                  " m/s"
                )
              }
            </strong>
          </div>

        </div>
      `;


      locations.appendChild(
        card
      );
    }
  );


  // ======================================
  // 물류 노선
  // ======================================

  const routes =
    $("routesList");

  routes.innerHTML = "";


  (
    data.routes || []
  ).forEach(
    (route) => {

      const risk =
        route.risk || {};


      const row =
        document.createElement(
          "div"
        );


      row.className =
        "route-row";


      row.innerHTML = `
        <div>

          <div class="route-title">
            ${
              safe(
                route.name_ko
              )
            }
          </div>

          <div class="route-reason">
            ${
              safe(
                route.reason_ko
              )
            }
          </div>

        </div>


        <div
          class="
            risk-pill
            ${
              riskClass(
                risk.label_ko
              )
            }
          "
        >
          ${
            safe(
              risk.emoji
            )
          }

          ${
            safe(
              risk.label_ko
            )
          }
        </div>
      `;


      routes.appendChild(
        row
      );
    }
  );



  // ======================================
  // 업데이트 / 출처
  // ======================================

  $("updatedAt").textContent =
    `업데이트 ${
      fullTime(
        data.generated_at_utc
      )
    } UTC`;


  $("footerUpdated").textContent =
    `최종 업데이트: ${
      fullTime(
        data.generated_at_utc
      )
    } UTC`;


  $("attribution").textContent =
    `데이터 출처: ${
      (
        data.attribution || []
      ).join(" · ")
    }`;
}


// ==========================================
// 새로고침
// ==========================================

$("refreshBtn")
  .addEventListener(
    "click",
    () => {

      loadDashboard()
        .catch(
          (error) => {

            $("updatedAt")
              .textContent =
                `오류: ${error.message}`;
          }
        );
    }
  );


// ==========================================
// 최초 실행
// ==========================================

loadDashboard()
  .catch(
    (error) => {

      $("updatedAt")
        .textContent =
          `데이터 로드 실패: ${error.message}`;
    }
  );
