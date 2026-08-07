/* 空调选购数据库 SPA 逻辑：筛选/排序/多源优先 */
"use strict";

const DATA_URL = "data/latest.json";
const SORT_FIELDS = [
  { key: "source_count", label: "数据来源数（多源优先）" },
  { key: "apf", label: "APF 能效比" },
  { key: "air_flow", label: "循环风量" },
  { key: "indoor_noise_max", label: "内机噪音（低优先）" },
  { key: "price", label: "价格" },
];

let rows = [];
let filters = {};       // key -> Set(active values)
let required = {};      // key -> required value
let sortKey = "source_count";
let sortDir = "desc";
const defaultSort = { key: "source_count", dir: "desc" };

const NOISE_RE = /([\d.]+)\s*dB/i;

function numValue(item, key) {
  const v = item[key];
  if (v === null || v === undefined || v === "") return null;
  const n = parseFloat(String(v).replace(/[^\d.\-]/g, ""));
  return Number.isFinite(n) ? n : null;
}

function noiseMax(item) {
  const raw = item.indoor_noise || item.indoor_noise_raw || "";
  const parts = String(raw).match(/([\d.]+)/g);
  if (!parts) return null;
  return Math.max(...parts.map(Number));
}

function applyFilters() {
  return rows.filter((item) => {
    for (const [key, values] of Object.entries(filters)) {
      const itemValue = item[key];
      if (!values.has(String(itemValue === null || itemValue === undefined ? "未知" : itemValue))) {
        return false;
      }
    }
    for (const [key, value] of Object.entries(required)) {
      const itemValue = item[key];
      if (Array.isArray(value)) {
        if (!value.includes(itemValue)) return false;
      } else if (itemValue !== value) {
        return false;
      }
    }
    return true;
  });
}

function compareRows(a, b) {
  // 默认任何排序下多源在前（source_count 显式排序时尊重方向）
  const aCount = numValue(a, "source_count") || 0;
  const bCount = numValue(b, "source_count") || 0;
  if (sortKey !== "source_count") {
    if (bCount !== aCount) return bCount - aCount;
  }
  let av = numValue(a, sortKey);
  let bv = numValue(b, sortKey);
  if (sortKey === "indoor_noise_max") {
    av = noiseMax(a); bv = noiseMax(b);
  }
  if (av === null && bv === null) return String(a.identity_key || "").localeCompare(String(b.identity_key || ""));
  if (av === null) return 1;   // 未知排最后
  if (bv === null) return -1;
  const cmp = av - bv;
  return sortDir === "desc" ? -cmp : cmp;
}

function renderFilters() {
  const bar = document.getElementById("filter-bar");
  bar.innerHTML = "";
  const groups = [
    { key: "brand", label: "品牌", multi: true },
    { key: "ac_type", label: "类型", multi: false },
    { key: "hp", label: "匹数", multi: true },
    { key: "energy_grade", label: "能效等级", multi: true },
    { key: "throttle_type", label: "节流装置", multi: false },
    { key: "coil_rows", label: "铜管排数", multi: false },
    { key: "refrigerant", label: "制冷剂", multi: true },
  ];
  for (const group of groups) {
    const values = [...new Set(rows.map((r) => r[group.key]).filter((v) => v !== null && v !== undefined && v !== ""))];
    values.sort((x, y) => String(x).localeCompare(String(y), "zh"));
    if (!values.length) continue;
    const div = document.createElement("div");
    div.className = "filter-group";
    const label = document.createElement("label");
    label.textContent = group.label;
    div.appendChild(label);
    const opts = document.createElement("div");
    opts.className = "options";
    for (const value of values) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = value;
      chip.dataset.key = group.key;
      chip.dataset.value = value;
      chip.addEventListener("click", () => {
        const current = filters[group.key] || new Set();
        if (current.has(String(value))) current.delete(String(value));
        else {
          if (!group.multi) current.clear();
          current.add(String(value));
        }
        filters[group.key] = current;
        renderAll();
      });
      opts.appendChild(chip);
    }
    div.appendChild(opts);
    bar.appendChild(div);
  }
}

function renderSort() {
  const select = document.getElementById("sort-select");
  select.innerHTML = "";
  for (const field of SORT_FIELDS) {
    const option = document.createElement("option");
    option.value = field.key;
    option.textContent = field.label;
    if (field.key === sortKey) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener("change", () => {
    sortKey = select.value;
    renderTable();
  });
  document.getElementById("sort-dir").addEventListener("change", (e) => {
    sortDir = e.target.value;
    renderTable();
  });
}

function renderTable() {
  const filtered = applyFilters().sort(compareRows);
  const thead = document.getElementById("table-head");
  const tbody = document.getElementById("table-body");
  const columns = [
    { key: "title", label: "型号" },
    { key: "brand", label: "品牌" },
    { key: "ac_type", label: "类型" },
    { key: "hp", label: "匹数" },
    { key: "cooling_capacity", label: "制冷量(W)" },
    { key: "heating_capacity", label: "制热量(W)" },
    { key: "air_flow", label: "循环风量" },
    { key: "apf", label: "APF" },
    { key: "energy_grade", label: "能效" },
    { key: "indoor_noise", label: "内机噪音" },
    { key: "throttle_type", label: "节流装置" },
    { key: "coil_rows", label: "铜管排数" },
    { key: "refrigerant", label: "制冷剂" },
    { key: "price", label: "价格(¥)" },
    { key: "launch_date", label: "上市" },
    { key: "sources", label: "数据来源" },
  ];
  thead.innerHTML = columns.map((c) => `<th data-key="${c.key}">${c.label}</th>`).join("");
  thead.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (key === "sources") return;
      if (key === sortKey) sortDir = sortDir === "desc" ? "asc" : "desc";
      else { sortKey = key; sortDir = "desc"; }
      document.getElementById("sort-select").value = sortKey;
      document.getElementById("sort-dir").value = sortDir;
      renderTable();
    });
  });
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="' + columns.length + '" class="unknown">无匹配机型</td></tr>';
  } else {
    tbody.innerHTML = filtered.map((item) => {
      const sources = (item.atomic_source_names || []).map((s) => `<span class="source-tag">${s}</span>`).join("");
      return "<tr>" + columns.map((c) => {
        let value = item[c.key];
        let cls = "";
        if (c.key === "sources") return `<td>${sources}</td>`;
        if (c.key === "title") {
          value = `${item.brand || ""}${item.model || ""}`;
        }
        if (c.key === "throttle_type") {
          cls = value === "电子膨胀阀" ? "good" : value === "毛细管" ? "warn" : "unknown";
        } else if (c.key === "coil_rows") {
          cls = value === "双排" || value === "1.6排" ? "good" : value === "单排" ? "warn" : "unknown";
        } else if (c.key === "inverter") {
          cls = value === true ? "good" : "bad";
        }
        if (value === null || value === undefined || value === "") {
          value = "未知";
          cls = "unknown";
        }
        return `<td class="${cls}">${String(value)}</td>`;
      }).join("") + "</tr>";
    }).join("");
  }
  document.getElementById("result-count").textContent = `${filtered.length} / ${rows.length} 款`;
}

function renderAll() {
  renderFilters();
  renderTable();
}

fetch(DATA_URL)
  .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then((data) => {
    rows = Array.isArray(data.items) ? data.items : [];
    required = { inverter: true, ac_type: ["壁挂式", "立柜式"] };
    filters = { throttle_type: new Set(["电子膨胀阀"]) };
    sortKey = defaultSort.key;
    sortDir = defaultSort.dir;
    renderSort();
    renderAll();
  })
  .catch((err) => {
    document.getElementById("table-body").innerHTML =
      `<tr><td colspan="16" class="unknown">数据加载失败：${err.message}（部署工作流可能尚未生成数据）</td></tr>`;
  });
