/* @ds-bundle: {"format":3,"namespace":"DepthFusionDesignSystem_ab16e1","components":[{"name":"LogoMark","sourcePath":"components/brand/LogoMark.jsx"},{"name":"Avatar","sourcePath":"components/core/Avatar.jsx"},{"name":"Badge","sourcePath":"components/core/Badge.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Card","sourcePath":"components/core/Card.jsx"},{"name":"NodeChip","sourcePath":"components/data/NodeChip.jsx"},{"name":"ResultCard","sourcePath":"components/data/ResultCard.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"Radio","sourcePath":"components/forms/Radio.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"}],"sourceHashes":{"components/brand/LogoMark.jsx":"4949a36bad4d","components/core/Avatar.jsx":"f18f352c0905","components/core/Badge.jsx":"d65fe55e8a95","components/core/Button.jsx":"ead0ecf3e0f6","components/core/Card.jsx":"5866d5dcee1a","components/data/NodeChip.jsx":"3e1d1a9b944e","components/data/ResultCard.jsx":"d8563379aedb","components/forms/Checkbox.jsx":"dcdc9bfed632","components/forms/Input.jsx":"b2fd23b1bd12","components/forms/Radio.jsx":"da7534e78ca7","components/navigation/Tabs.jsx":"2eb3451b5cba"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.DepthFusionDesignSystem_ab16e1 = window.DepthFusionDesignSystem_ab16e1 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/brand/LogoMark.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * DepthFusion LogoMark — the geometric memory-graph mark on a rounded plate.
 * Three outer nodes (a triangle) + center hub + spokes. Also the brand's
 * animation artifact: pass `animation` to make it breathe / develop / pulse / draw.
 */
function LogoMark({
  size = 32,
  plate = 'var(--accent)',
  mark = 'var(--on-accent)',
  flat = false,
  animation = '',
  className = '',
  ...rest
}) {
  const anims = String(animation).split(/\s+/).filter(Boolean).map(a => `df-logo--${a}`).join(' ');
  const cls = ['df-logo', anims, className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("span", _extends({
    className: cls
  }, rest), /*#__PURE__*/React.createElement("svg", {
    width: size,
    height: size,
    viewBox: "0 0 64 64",
    fill: "none",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("g", {
    className: "df-logo__mark"
  }, /*#__PURE__*/React.createElement("rect", {
    width: "64",
    height: "64",
    rx: "14",
    fill: plate
  }), !flat && /*#__PURE__*/React.createElement("g", {
    stroke: mark,
    strokeOpacity: "0.55",
    strokeWidth: "1.5"
  }, /*#__PURE__*/React.createElement("line", {
    className: "df-logo__spoke",
    x1: "32",
    y1: "32",
    x2: "32",
    y2: "10"
  }), /*#__PURE__*/React.createElement("line", {
    className: "df-logo__spoke",
    x1: "32",
    y1: "32",
    x2: "14",
    y2: "50"
  }), /*#__PURE__*/React.createElement("line", {
    className: "df-logo__spoke",
    x1: "32",
    y1: "32",
    x2: "50",
    y2: "50"
  })), !flat && /*#__PURE__*/React.createElement("polygon", {
    points: "32,10 14,50 50,50",
    fill: "none",
    stroke: mark,
    strokeOpacity: "0.3",
    strokeWidth: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    className: "df-logo__node",
    cx: "32",
    cy: "10",
    r: "5",
    fill: mark
  }), /*#__PURE__*/React.createElement("circle", {
    className: "df-logo__node",
    cx: "14",
    cy: "50",
    r: "5",
    fill: mark
  }), /*#__PURE__*/React.createElement("circle", {
    className: "df-logo__node",
    cx: "50",
    cy: "50",
    r: "5",
    fill: mark
  }), /*#__PURE__*/React.createElement("circle", {
    className: "df-logo__hub",
    cx: "32",
    cy: "32",
    r: "7",
    fill: mark
  }))));
}
Object.assign(__ds_scope, { LogoMark });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/LogoMark.jsx", error: String((e && e.message) || e) }); }

// components/core/Avatar.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Circular initial avatar (amber fill, ember glow). Derives the initial from `name`. */
function Avatar({
  name = '',
  size = 46,
  className = '',
  ...rest
}) {
  const initial = (String(name).trim()[0] || '?').toUpperCase();
  return /*#__PURE__*/React.createElement("div", _extends({
    className: ['df-avatar', className].filter(Boolean).join(' '),
    style: {
      width: size,
      height: size,
      fontSize: Math.round(size * 0.41)
    }
  }, rest), initial);
}
Object.assign(__ds_scope, { Avatar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Avatar.jsx", error: String((e && e.message) || e) }); }

// components/core/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Small status pill. Classification variants (public/internal/confidential/
 * restricted) plus a neutral `source` variant for source-type tags.
 */
function Badge({
  variant = 'source',
  children,
  className = '',
  ...rest
}) {
  const cls = ['df-badge', `df-badge--${variant}`, className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("span", _extends({
    className: cls
  }, rest), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Badge.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Primary action control. Variants: primary · secondary · danger · ghost. */
function Button({
  variant = 'primary',
  children,
  className = '',
  ...rest
}) {
  const cls = ['df-btn', `df-btn--${variant}`, className].filter(Boolean).join(' ');
  return /*#__PURE__*/React.createElement("button", _extends({
    className: cls
  }, rest), children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Surface container with an optional title. Used for panels, settings sections, tiles. */
function Card({
  title,
  children,
  className = '',
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    className: ['df-card', className].filter(Boolean).join(' ')
  }, rest), title ? /*#__PURE__*/React.createElement("div", {
    className: "df-card__title"
  }, title) : null, children);
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Card.jsx", error: String((e && e.message) || e) }); }

// components/data/NodeChip.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const META = {
  doc: {
    cls: 'doc',
    label: 'Document'
  },
  concept: {
    cls: 'con',
    label: 'Concept'
  },
  decision: {
    cls: 'dec',
    label: 'Decision'
  }
};

/** Knowledge-graph node-type chip. `type`: doc · concept · decision. */
function NodeChip({
  type = 'doc',
  children,
  className = '',
  ...rest
}) {
  const m = META[type] || META.doc;
  return /*#__PURE__*/React.createElement("span", _extends({
    className: ['df-chip', `df-chip--${m.cls}`, className].filter(Boolean).join(' ')
  }, rest), children || m.label);
}
Object.assign(__ds_scope, { NodeChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/NodeChip.jsx", error: String((e && e.message) || e) }); }

// components/data/ResultCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function renderSnippet(s) {
  return String(s).split(/(\{[^}]+\})/g).map((p, i) => p.startsWith('{') && p.endsWith('}') ? /*#__PURE__*/React.createElement("mark", {
    key: i
  }, p.slice(1, -1)) : /*#__PURE__*/React.createElement(React.Fragment, {
    key: i
  }, p));
}
function scoreColor(n) {
  return n > 80 ? 'var(--ok-soft)' : n >= 50 ? 'var(--accent)' : 'var(--warn)';
}

/**
 * Search result card. `result` = { title, cls, source, snippet, score, date, loc }.
 * Wrap query terms in {curly braces} inside `snippet` to highlight them.
 */
function ResultCard({
  result,
  className = '',
  ...rest
}) {
  const r = result || {};
  return /*#__PURE__*/React.createElement("div", _extends({
    className: ['df-result', className].filter(Boolean).join(' '),
    role: "article",
    tabIndex: 0
  }, rest), /*#__PURE__*/React.createElement("div", {
    className: "df-result__top"
  }, /*#__PURE__*/React.createElement("div", {
    className: "df-result__title"
  }, r.title), /*#__PURE__*/React.createElement("div", {
    className: "df-badges"
  }, r.cls ? /*#__PURE__*/React.createElement("span", {
    className: `df-badge df-badge--${r.cls}`
  }, r.cls) : null, r.source ? /*#__PURE__*/React.createElement("span", {
    className: "df-badge df-badge--source"
  }, r.source) : null)), r.snippet ? /*#__PURE__*/React.createElement("div", {
    className: "df-result__snip"
  }, renderSnippet(r.snippet)) : null, /*#__PURE__*/React.createElement("div", {
    className: "df-result__foot"
  }, typeof r.score === 'number' ? /*#__PURE__*/React.createElement("div", {
    className: "df-score"
  }, /*#__PURE__*/React.createElement("div", {
    className: "df-score__bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "df-score__fill",
    style: {
      width: r.score + '%',
      background: scoreColor(r.score)
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "df-score__pct"
  }, r.score, "%")) : null, r.date ? /*#__PURE__*/React.createElement("span", {
    className: "df-result__date"
  }, r.date) : null, r.loc ? /*#__PURE__*/React.createElement("span", {
    className: "df-result__loc"
  }, r.loc) : null));
}
Object.assign(__ds_scope, { ResultCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/ResultCard.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Labeled checkbox option (facet-panel style). */
function Checkbox({
  label,
  className = '',
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: ['df-opt', className].filter(Boolean).join(' ')
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox"
  }, rest)), label);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Text input. Pass `icon` to render a leading icon (e.g. the search field). */
function Input({
  icon,
  className = '',
  ...rest
}) {
  if (icon) {
    return /*#__PURE__*/React.createElement("span", {
      className: "df-input-wrap"
    }, /*#__PURE__*/React.createElement("span", {
      className: "df-search-ic"
    }, icon), /*#__PURE__*/React.createElement("input", _extends({
      className: ['df-input', className].filter(Boolean).join(' ')
    }, rest)));
  }
  return /*#__PURE__*/React.createElement("input", _extends({
    className: ['df-input', 'df-input--plain', className].filter(Boolean).join(' ')
  }, rest));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/Radio.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Labeled radio option (facet-panel style). Pass a shared `name` to group. */
function Radio({
  label,
  className = '',
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: ['df-opt', className].filter(Boolean).join(' ')
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "radio"
  }, rest)), label);
}
Object.assign(__ds_scope, { Radio });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Radio.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
/**
 * Horizontal tab/segment nav. `tabs` is an array of strings or {id,label}.
 * Controlled: pass `value` and `onChange(id)`.
 */
function Tabs({
  tabs = [],
  value,
  onChange,
  className = ''
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: ['df-tabs', className].filter(Boolean).join(' ')
  }, tabs.map(t => {
    const id = typeof t === 'string' ? t : t.id;
    const label = typeof t === 'string' ? t : t.label;
    return /*#__PURE__*/React.createElement("button", {
      key: id,
      className: 'df-tab' + (value === id ? ' df-tab--active' : ''),
      onClick: () => onChange && onChange(id)
    }, label);
  }));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

__ds_ns.LogoMark = __ds_scope.LogoMark;

__ds_ns.Avatar = __ds_scope.Avatar;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.NodeChip = __ds_scope.NodeChip;

__ds_ns.ResultCard = __ds_scope.ResultCard;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Radio = __ds_scope.Radio;

__ds_ns.Tabs = __ds_scope.Tabs;

})();
