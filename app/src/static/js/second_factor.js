/* Passkey (WebAuthn) ceremonies for sso.pdhc (#1040).
 *
 * The server builds the options and verifies the answer (py_webauthn); this
 * file only moves bytes between the server's JSON (base64url) and the
 * browser's ArrayBuffers. No library, no third-party origin.
 *
 * Wiring is by data attributes, so the page needs no inline script:
 *   <button data-passkey="register" data-options="/…" data-verify="/…">
 *   <button data-passkey="login"    data-options="/…" data-verify="/…">
 * The CSRF token is read from <meta name="csrf-token">.
 */
(function () {
  'use strict';

  function b64urlToBuf(s) {
    var b = s.replace(/-/g, '+').replace(/_/g, '/');
    while (b.length % 4) { b += '='; }
    var raw = atob(b), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) { out[i] = raw.charCodeAt(i); }
    return out.buffer;
  }

  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf), s = '';
    for (var i = 0; i < bytes.length; i++) { s += String.fromCharCode(bytes[i]); }
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) { throw new Error(j.message || j.error || ('HTTP ' + r.status)); }
        return j;
      });
    });
  }

  function descriptors(list) {
    return (list || []).map(function (c) {
      return {type: c.type, id: b64urlToBuf(c.id), transports: c.transports};
    });
  }

  function register(optionsUrl, verifyUrl, name) {
    return post(optionsUrl).then(function (o) {
      o.challenge = b64urlToBuf(o.challenge);
      o.user.id = b64urlToBuf(o.user.id);
      o.excludeCredentials = descriptors(o.excludeCredentials);
      return navigator.credentials.create({publicKey: o});
    }).then(function (cred) {
      var r = cred.response;
      return post(verifyUrl, {name: name, credential: {
        id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment || undefined,
        response: {
          clientDataJSON: bufToB64url(r.clientDataJSON),
          attestationObject: bufToB64url(r.attestationObject),
          transports: r.getTransports ? r.getTransports() : undefined
        }
      }});
    });
  }

  function login(optionsUrl, verifyUrl) {
    return post(optionsUrl).then(function (o) {
      o.challenge = b64urlToBuf(o.challenge);
      o.allowCredentials = descriptors(o.allowCredentials);
      return navigator.credentials.get({publicKey: o});
    }).then(function (cred) {
      var r = cred.response;
      return post(verifyUrl, {
        id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
        response: {
          clientDataJSON: bufToB64url(r.clientDataJSON),
          authenticatorData: bufToB64url(r.authenticatorData),
          signature: bufToB64url(r.signature),
          userHandle: r.userHandle ? bufToB64url(r.userHandle) : undefined
        }
      });
    });
  }

  function show(msg) {
    var el = document.getElementById('passkey-message');
    if (el) { el.textContent = msg; el.hidden = false; }
  }

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('[data-passkey]') : null;
    if (!btn) { return; }
    ev.preventDefault();
    if (!window.PublicKeyCredential) {
      show('Den här webbläsaren stöder inte passkeys. Använd en autentiseringsapp i stället.');
      return;
    }
    btn.disabled = true;
    var nameEl = document.getElementById('passkey-name');
    var job = btn.getAttribute('data-passkey') === 'register'
      ? register(btn.dataset.options, btn.dataset.verify, nameEl ? nameEl.value : '')
      : login(btn.dataset.options, btn.dataset.verify);
    job.then(function (res) {
      window.location.assign(res.redirect || '/dashboard');
    }).catch(function (err) {
      btn.disabled = false;
      show('Det gick inte: ' + (err && err.message ? err.message : 'okänt fel'));
    });
  });
})();
