// Tiny DOM helpers. Vanilla JS, no framework, no innerHTML for user text.
"use strict";

// Create an element. `attrs` may include: class, id, type, value, placeholder,
// text (textContent), html (trusted static markup only), on (event map), and
// any other key set as an attribute. `children` are appended in order.
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (val === null || val === undefined || val === false) continue;
    if (key === "class") node.className = val;
    else if (key === "text") node.textContent = val;
    else if (key === "html") node.innerHTML = val;
    else if (key === "value") node.value = val;
    else if (key === "on") {
      for (const [evt, fn] of Object.entries(val)) node.addEventListener(evt, fn);
    } else if (key === "dataset") {
      for (const [d, dv] of Object.entries(val)) node.dataset[d] = dv;
    } else if (val === true) {
      node.setAttribute(key, "");
    } else {
      node.setAttribute(key, val);
    }
  }
  const kids = Array.isArray(children) ? children : [children];
  for (const child of kids) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(
      typeof child === "string" ? document.createTextNode(child) : child
    );
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// Build an <option> list for a <select>. `selected` matches by value.
export function fillSelect(select, options, selected) {
  clear(select);
  for (const opt of options) {
    const o = el("option", { value: opt.value, text: opt.label });
    if (opt.value === selected) o.selected = true;
    select.appendChild(o);
  }
}

// Toast-ish status line. Returns a function to set text + level.
export function statusSetter(node) {
  return (text, level) => {
    node.textContent = text;
    node.className = "status" + (level ? " " + level : "");
  };
}
