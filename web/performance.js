const chartDate = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});

export function durationText(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return minutes ? `${minutes}m ${total % 60}s` : `${total}s`;
}

export function rateText(rate) {
  return `${Number(rate.toFixed(1))}%`;
}

function svgNode(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

export function trend(samples, field, format, label) {
  const values = samples.map((sample) => sample[field]).filter(Number.isFinite);
  if (!values.length) return null;
  const limit = field === "rate" ? 100 : Math.max(1, ...values) * 1.08;
  const chart = svgNode("svg", {
    class: `trend trend-${field}`, viewBox: "0 0 134 32", role: "img",
    "aria-label": `${label}, ${format(values[0])} to ${format(values.at(-1))} over the recent results`,
  });
  chart.append(svgNode("line", { class: "trend-baseline", x1: 4, x2: 130, y1: 27, y2: 27 }));
  const line = svgNode("path", { class: "trend-line" });
  chart.append(line);
  let path = "";
  let previous = false;
  for (const [index, sample] of samples.entries()) {
    const value = sample[field];
    if (value == null) {
      previous = false;
      continue;
    }
    const x = samples.length === 1 ? 67 : 4 + 126 * index / (samples.length - 1);
    const y = 27 - 23 * value / limit;
    path += `${previous ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)} `;
    previous = true;
    const dot = svgNode("circle", { class: "trend-point", cx: x, cy: y, r: 2.8 });
    const title = svgNode("title");
    title.textContent = `${chartDate.format(sample.at * 1000)} · ${format(value)}`;
    dot.append(title);
    chart.append(dot);
  }
  line.setAttribute("d", path.trim());
  return chart;
}
