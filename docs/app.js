/* 空调选购数据库 SPA 逻辑：多级排序（Excel 风格）/筛选/多源优先 */
"use strict";

const DATA_URL = "data/latest.json";
const MAX_SORT_LEVELS = 4;

const SORT_FIELDS = [
  { key: "source_count", label: "数据来源数（多源优先）" },
  { key: "apf", label: "APF 能效比" },
  { key: "air_flow", label: "循环风量" },
  { key: "cooling_capacity", label: "制冷量" },
  { key: "heating_capacity", label: "制热量" },
  { key: "cooling_power", label: "制冷功率" },
  { key: "heating_power", label: "制热功率" },
  { key: "indoor_noise_max", label: "内机噪音（低优先）" },
  { key: "outdoor_noise", label: "外机噪音（低优先）" },
  { key: "price", label: "价格" },
  { key: "launch_date", label: "上市时间" },
];

let rows = [];
let filters = {};       // key -> Set(active values)
let required = {};      // key -> required value
let numericRanges = {}; // key -> {min?, max?}  (Excel 风格数值范围筛选)
let sortLevels = [];    // [{key, dir}] 多级排序，最多 4 级
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

/* 按排序级别取数值；launch_date('2025-03') 转可比较数 */
function levelValue(item, key) {
  if (key === "indoor_noise_max") return noiseMax(item);
  if (key === "launch_date") {
    const m = String(item.launch_date || "").match(/(\d{4})(?:-(\d{2}))?/);
    return m ? (m[1] + (m[2] || "00")) * 1 : null;
  }
  return numValue(item, key);
}

/* Excel 风格多级排序：按 sortLevels 依次比较，前级相同才比后级 */
function compareRows(a, b) {
  const aCount = numValue(a, "source_count") || 0;
  const bCount = numValue(b, "source_count") || 0;
  // 隐式多源优先：默认任何排序下多源在前；仅当第一级显式为 source_count 时尊重方向
  const firstIsSourceCount = sortLevels.length > 0 && sortLevels[0].key === "source_count";
  if (!firstIsSourceCount && bCount !== aCount) return bCount - aCount;
  for (const level of sortLevels) {
    const av = levelValue(a, level.key);
    const bv = levelValue(b, level.key);
    if (av === null && bv === null) continue;
    if (av === null) return level.dir === "desc" ? 1 : -1; // 空值排最后
    if (bv === null) return level.dir === "desc" ? -1 : 1;
    const cmp = av - bv;
    if (cmp !== 0) return level.dir === "desc" ? -cmp : cmp;
  }
  return String(a.identity_key || "").localeCompare(String(b.identity_key || ""));
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
    // Excel 风格数值范围筛选
    for (const [key, range] of Object.entries(numericRanges)) {
      const v = key === "indoor_noise_max" ? noiseMax(item) : numValue(item, key);
      if (v === null) return false; // 无值不满足范围
      if (range.min !== undefined && range.min !== "" && v < Number(range.min)) return false;
      if (range.max !== undefined && range.max !== "" && v > Number(range.max)) return false;
    }
    return true;
  });
}

/* ── UI 渲染 ─────────────────────────────────────────── */

function renderSortLevels() {
  const container = document.getElementById("sort-levels");
  if (!container) return;
  container.innerHTML = "";
  sortLevels.forEach((level, idx) => {
    const row = document.createElement("div");
    row.className = "sort-level";
    const order = document.createElement("span");
    order.className = "sort-level-order";
    order.textContent = "关键字 " + (idx + 1);
    const fieldSel = document.createElement("select");
    SORT_FIELDS.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.key;
      opt.textContent = f.label;
      if (f.key === level.key) opt.selected = true;
      fieldSel.appendChild(opt);
    });
    fieldSel.addEventListener("change", () => {
      sortLevels[idx].key = fieldSel.value;
      renderTable();
    });
    const dirSel = document.createElement("select");
    ["desc", "asc"].forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d === "desc" ? "降序" : "升序";
      if (d === level.dir) opt.selected = true;
      dirSel.appendChild(opt);
    });
    dirSel.addEventListener("change", () => {
      sortLevels[idx].dir = dirSel.value;
      renderTable();
    });
    const removeBtn = document.createElement("button");
    removeBtn.className = "sort-level-remove";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => {
      sortLevels.splice(idx, 1);
      renderSortLevels();
      renderTable();
    });
    row.appendChild(order);
    row.appendChild(fieldSel);
    row.appendChild(dirSel);
    row.appendChild(removeBtn);
    container.appendChild(row);
  });
  const addBtn = document.getElementById("add-sort-level");
  if (addBtn) addBtn.style.display = sortLevels.length >= MAX_SORT_LEVELS ? "none" : "inline-block";
}

function addSortLevel() {
  if (sortLevels.length >= MAX_SORT_LEVELS) return;
  // 下一级默认选未使用的字段
  const used = new Set(sortLevels.map((l) => l.key));
  const next = SORT_FIELDS.find((f) => !used.has(f.key)) || SORT_FIELDS[0];
  sortLevels.push({ key: next.key, dir: "desc" });
  renderSortLevels();
  renderTable();
}

function renderNumericRanges() {
  const container = document.getElementById("numeric-ranges");
  if (!container) return;
  container.innerHTML = "";
  const rangeDefs = [
    { key: "apf", label: "APF", min: true, max: false, placeholder: "≥" },
    { key: "air_flow", label: "循环风量", min: true, max: false, placeholder: "≥" },
    { key: "indoor_noise_max", label: "内机噪音", min: false, max: true, placeholder: "≤" },
    { key: "outdoor_noise", label: "外机噪音", min: false, max: true, placeholder: "≤" },
    { key: "price", label: "价格(¥)", min: true, max: true, placeholder: "" },
    { key: "cooling_capacity", label: "制冷量", min: true, max: false, placeholder: "≥" },
  ];
  for (const def of rangeDefs) {
    const group = document.createElement("div");
    group.className = "filter-group range-group";
    const label = document.createElement("label");
    label.textContent = def.label;
    group.appendChild(label);
    const opts = document.createElement("div");
    opts.className = "options";
    const range = numericRanges[def.key] || (numericRanges[def.key] = {});
    if (def.min) {
      const input = document.createElement("input");
      input.type = "number";
      input.className = "range-input";
      input.placeholder = (def.placeholder || "") + "最小值";
      input.value = range.min !== undefined ? range.min : "";
      input.addEventListener("input", () => {
        range.min = input.value;
        renderTable();
      });
      opts.appendChild(input);
    }
    if (def.max) {
      const input = document.createElement("input");
      input.type = "number";
      input.className = "range-input";
      input.placeholder = (def.placeholder || "") + "最大值";
      input.value = range.max !== undefined ? range.max : "";
      input.addEventListener("input", () => {
        range.max = input.value;
        renderTable();
      });
      opts.appendChild(input);
    }
    const clear = document.createElement("button");
    clear.className = "range-clear";
    clear.textContent = "清";
    clear.addEventListener("click", () => {
      numericRanges[def.key] = {};
      renderNumericRanges();
      renderTable();
    });
    opts.appendChild(clear);
    group.appendChild(opts);
    container.appendChild(group);
  }
}

function sortFilterValues(key, values) {
  if (key === "hp") {
    // 匹数按数值序：1匹 < 大1匹 < 1.5匹 < 2匹 < 3匹（大+0.1 / 小-0.1 偏移）
    const keyOf = (v) => {
      const m = String(v).match(/(\d+(?:\.\d+)?)/);
      if (!m) return 999;
      let n = parseFloat(m[1]);
      if (String(v).startsWith("大")) n += 0.1;
      if (String(v).startsWith("小")) n -= 0.1;
      return n;
    };
    return [...values].sort((x, y) => keyOf(x) - keyOf(y) ||
      String(x).localeCompare(String(y), "zh"));
  }
  if (key === "energy_grade") {
    // 能效等级：新一级放最前，其余按数值（1级<2级<3级）
    const keyOf = (v) => {
      if (v === "新一级") return 0.5;
      const m = String(v).match(/(\d+)/);
      return m ? parseFloat(m[1]) : 999;
    };
    return [...values].sort((x, y) => keyOf(x) - keyOf(y));
  }
  return [...values].sort((x, y) => String(x).localeCompare(String(y), "zh"));
}

function renderFilters() {
  const bar = document.getElementById("filter-bar");
  if (!bar) return;
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
    if (!values.length) continue;
    const ordered = sortFilterValues(group.key, values);
    const div = document.createElement("div");
    div.className = "filter-group";
    const label = document.createElement("label");
    label.textContent = group.label;
    div.appendChild(label);
    const opts = document.createElement("div");
    opts.className = "options";
    const current = filters[group.key] || new Set();
    for (const value of ordered) {
      const chip = document.createElement("span");
      // 选中态差异显示：已选中的项加 active class（浅色背景高亮）
      const isActive = current.has(String(value));
      chip.className = "chip" + (isActive ? " active" : "");
      chip.textContent = value;
      chip.dataset.key = group.key;
      chip.dataset.value = value;
      chip.addEventListener("click", () => {
        const cur = filters[group.key] || new Set();
        if (cur.has(String(value))) cur.delete(String(value));
        else {
          if (!group.multi) cur.clear();
          cur.add(String(value));
        }
        filters[group.key] = cur;
        renderAll();
      });
      opts.appendChild(chip);
    }
    div.appendChild(opts);
    bar.appendChild(div);
  }
}

function renderTable() {
  const filtered = applyFilters().sort(compareRows);
  const thead = document.getElementById("table-head");
  const tbody = document.getElementById("table-body");
  if (!thead || !tbody) return;
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
      // 点击表头：作为第一关键字（同 key 则翻转方向）
      if (sortLevels.length > 0 && sortLevels[0].key === key) {
        sortLevels[0].dir = sortLevels[0].dir === "desc" ? "asc" : "desc";
      } else {
        sortLevels = [{ key, dir: "desc" }];
      }
      renderSortLevels();
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
  const countEl = document.getElementById("result-count");
  if (countEl) countEl.textContent = `${filtered.length} / ${rows.length} 款`;
}

function renderAll() {
  renderFilters();
  renderNumericRanges();
  renderTable();
}

fetch(DATA_URL)
  .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then((data) => {
    rows = Array.isArray(data.items) ? data.items : [];
    required = { inverter: true, ac_type: ["壁挂式", "立柜式"] };
    filters = { throttle_type: new Set(["电子膨胀阀"]) };
    numericRanges = {};
    sortLevels = [{ key: defaultSort.key, dir: defaultSort.dir }];
    const addBtn = document.getElementById("add-sort-level");
    if (addBtn) addBtn.addEventListener("click", addSortLevel);
    renderSortLevels();
    renderAll();
  })
  .catch((err) => {
    const tbody = document.getElementById("table-body");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="16" class="unknown">数据加载失败：${err.message}（部署工作流可能尚未生成数据）</td></tr>`;
  });
