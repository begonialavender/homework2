(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("survey-data").textContent);
  var state = { answers: {}, current: data.first, submitting: false };

  var els = {
    intro: document.getElementById("survey-intro"),
    view: document.getElementById("question-view"),
    index: document.getElementById("q-index"),
    title: document.getElementById("q-title"),
    hint: document.getElementById("q-hint"),
    body: document.getElementById("q-body"),
    error: document.getElementById("fill-error"),
    progressBar: document.getElementById("progress-bar"),
    progressText: document.getElementById("q-progress-text"),
    next: document.getElementById("next-btn"),
    done: document.getElementById("done-view"),
    submitError: document.getElementById("submit-error"),
    submit: document.getElementById("submit-btn"),
    thanks: document.getElementById("thanks-view")
  };

  function show(el, on) {
    el.style.display = on ? "" : "none";
  }

  function setError(el, message) {
    if (!message) {
      el.textContent = "";
      el.classList.remove("show");
      return;
    }
    el.textContent = message;
    el.classList.add("show");
  }

  function ruleHint(q) {
    var r = q.rules || {};
    var parts = [];
    if (q.type === "multiple") {
      if (r.exact_choices != null) parts.push("必须选择 " + r.exact_choices + " 项");
      if (r.min_choices != null) parts.push("至少 " + r.min_choices + " 项");
      if (r.max_choices != null) parts.push("最多 " + r.max_choices + " 项");
    }
    if (q.type === "text") {
      if (r.min_length != null) parts.push("至少 " + r.min_length + " 字");
      if (r.max_length != null) parts.push("最多 " + r.max_length + " 字");
    }
    if (q.type === "number") {
      if (r.min_value != null) parts.push("不小于 " + r.min_value);
      if (r.max_value != null) parts.push("不大于 " + r.max_value);
      if (r.integer) parts.push("必须为整数");
    }
    return parts.length ? " · " + parts.join("，") : "";
  }

  function buildBody(q) {
    var html = "";
    if (q.type === "single" || q.type === "multiple") {
      var inputType = q.type === "single" ? "radio" : "checkbox";
      html += '<div class="options">';
      q.options.forEach(function (opt) {
        html +=
          '<label class="option"><input type="' + inputType + '" name="answer" value="' +
          opt.key + '"><span>' + opt.label + "</span></label>";
      });
      html += "</div>";
      return html;
    }
    if (q.type === "number") {
      var step = q.rules && q.rules.integer ? "1" : "any";
      return '<input type="number" step="' + step + '" id="answer-input" style="max-width:260px;">';
    }
    return '<textarea id="answer-input" rows="4"></textarea>';
  }

  function collect() {
    var q = state.current;
    if (q.type === "single") {
      var picked = document.querySelector('input[name="answer"]:checked');
      return picked ? picked.value : null;
    }
    if (q.type === "multiple") {
      var values = Array.prototype.map.call(
        document.querySelectorAll('input[name="answer"]:checked'),
        function (el) { return el.value; }
      );
      return values;
    }
    var input = document.getElementById("answer-input");
    if (!input) return null;
    var value = input.value.trim();
    return value === "" ? null : value;
  }

  function isEmpty(value) {
    return value == null || value === "" || (Array.isArray(value) && value.length === 0);
  }

  function render(q) {
    state.current = q;
    setError(els.error, "");
    els.index.textContent = "第 " + q.index + " 题 / 共 " + q.total + " 题";
    els.title.textContent = q.title;
    els.hint.textContent = (q.required ? "必答题" : "选答题") + ruleHint(q);
    els.body.innerHTML = buildBody(q);
    els.progressBar.style.width = Math.round(((q.index - 1) / q.total) * 100) + "%";
    els.progressText.textContent = "已完成 " + (q.index - 1) + " 题";
    show(els.view, true);
    show(els.intro, false);
    show(els.done, false);
  }

  function post(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json().then(function (body) {
        return { status: resp.status, body: body };
      });
    });
  }

  els.next.addEventListener("click", function () {
    var q = state.current;
    if (!q) return;
    var value = collect();
    if (q.required && isEmpty(value)) {
      setError(els.error, "本题为必答题，请先作答");
      return;
    }
    els.next.disabled = true;
    post("/api/surveys/" + encodeURIComponent(data.slug) + "/answer", {
      question_id: q.id,
      value: value,
      answers: state.answers
    }).then(function (res) {
      els.next.disabled = false;
      if (!res.body.ok) {
        setError(els.error, res.body.error || "答案不合法");
        return;
      }
      state.answers[q.id] = value;
      if (res.body.finished) {
        els.progressBar.style.width = "100%";
        show(els.view, false);
        show(els.done, true);
      } else {
        render(res.body.next);
      }
    }).catch(function () {
      els.next.disabled = false;
      setError(els.error, "网络异常，请重试");
    });
  });

  els.submit.addEventListener("click", function () {
    if (state.submitting) return;
    state.submitting = true;
    els.submit.disabled = true;
    post("/api/surveys/" + encodeURIComponent(data.slug) + "/submit", {
      answers: state.answers
    }).then(function (res) {
      if (!res.body.ok) {
        state.submitting = false;
        els.submit.disabled = false;
        setError(els.submitError, res.body.error || "提交失败");
        return;
      }
      show(els.done, false);
      show(els.thanks, true);
    }).catch(function () {
      state.submitting = false;
      els.submit.disabled = false;
      setError(els.submitError, "网络异常，请重试");
    });
  });

  if (data.first) {
    render(data.first);
  }
})();
