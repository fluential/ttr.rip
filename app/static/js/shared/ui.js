export function copyUrl(element, textToCopy) {
  if (!element) return;
  if (element.dataset.copying) return;
  element.dataset.copying = "true";
  const originalValue = element.value;
  const text = textToCopy || originalValue;

  navigator.clipboard
    .writeText(text)
    .then(() => {
      element.value = "Copied!";
      setTimeout(() => {
        element.value = originalValue;
        delete element.dataset.copying;
      }, 1200);
    })
    .catch(() => {
      element.value = originalValue;
      delete element.dataset.copying;
    });
}

export function debounce(func, delay) {
  let timeout;
  return function (...args) {
    const context = this;
    clearTimeout(timeout);
    timeout = setTimeout(() => func.apply(context, args), delay);
  };
}
