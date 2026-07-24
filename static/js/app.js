(function () {
  const stageEmpty = document.getElementById('stage-empty');
  const stagePhoto = document.getElementById('stage-photo');
  const arViewer = document.getElementById('ar-viewer');
  const stageLoading = document.getElementById('stage-loading');
  const stageLoadingText = document.getElementById('stage-loading-text');
  const stageError = document.getElementById('stage-error');

  const cameraInput = document.getElementById('camera-input');
  const btnCamera = document.getElementById('btn-camera');
  const btnAdd = document.getElementById('btn-add');
  const btnAr = document.getElementById('btn-ar');

  const navbar = document.getElementById('navbar');
  const navbarToggle = document.getElementById('navbar-toggle');

  const promptOverlay = document.getElementById('prompt-overlay');
  const promptInput = document.getElementById('prompt-input');
  const promptSubmit = document.getElementById('prompt-submit');

  let roomFile = null;
  let editedUrl = null;
  let lastPrompt = null;
  let errorTimer = null;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function showLoading(text) {
    stageLoadingText.textContent = text;
    stageLoading.hidden = false;
  }
  function hideLoading() {
    stageLoading.hidden = true;
  }
  function showError(message) {
    clearTimeout(errorTimer);
    stageError.textContent = message;
    stageError.hidden = false;
    errorTimer = setTimeout(() => { stageError.hidden = true; }, 5000);
  }

  function showEmpty() {
    stageEmpty.hidden = false;
    stagePhoto.hidden = true;
    arViewer.hidden = true;
  }
  function showPhoto(src) {
    stageEmpty.hidden = true;
    arViewer.hidden = true;
    stagePhoto.src = src;
    stagePhoto.hidden = false;
  }
  function showAr() {
    stageEmpty.hidden = true;
    stagePhoto.hidden = true;
    arViewer.hidden = false;
  }

  function resetDownstream() {
    editedUrl = null;
    lastPrompt = null;
    btnAr.disabled = true;
    arViewer.removeAttribute('src');
    arViewer.removeAttribute('ios-src');
  }

  // ---- Navbar collapse toggle ----
  navbarToggle.addEventListener('click', () => {
    navbar.classList.toggle('is-collapsed');
  });

  // ---- Step 1: Click Picture ----
  btnCamera.addEventListener('click', () => cameraInput.click());

  cameraInput.addEventListener('change', () => {
    const file = cameraInput.files[0];
    if (!file) return;
    roomFile = file;
    resetDownstream();
    promptOverlay.hidden = true;

    const reader = new FileReader();
    reader.onload = (e) => showPhoto(e.target.result);
    reader.readAsDataURL(file);

    btnAdd.disabled = false;
  });

  // ---- Step 2: Add Object ----
  btnAdd.addEventListener('click', () => {
    if (!roomFile) return;
    promptOverlay.hidden = !promptOverlay.hidden;
    if (!promptOverlay.hidden) {
      promptInput.value = '';
      setTimeout(() => promptInput.focus(), 50);
    }
  });

  async function submitPrompt() {
    const instruction = promptInput.value.trim();
    if (!instruction || !roomFile) return;

    promptOverlay.hidden = true;
    showLoading('Adding to your room…');

    try {
      const formData = new FormData();
      formData.append('room', roomFile);
      formData.append('prompt', instruction);

      const res = await fetch('/api/edit-room', { method: 'POST', body: formData });
      const data = await res.json();

      if (!res.ok || data.error) {
        throw new Error(data.error || 'Could not edit the photo.');
      }

      editedUrl = data.edited_url;
      lastPrompt = instruction;
      showPhoto(data.edited_url);
      btnAr.disabled = false;
    } catch (err) {
      showError(err.message);
      if (roomFile) showPhoto(stagePhoto.src);
    } finally {
      hideLoading();
    }
  }

  promptSubmit.addEventListener('click', submitPrompt);
  promptInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitPrompt();
  });

  // ---- Step 3/4: View AR/VR ----

  async function pollArJob(jobId, startedAt) {
    const POLL_INTERVAL_MS = 5000;
    const MAX_WAIT_MS = 15 * 60 * 1000;

    while (true) {
      if (Date.now() - startedAt > MAX_WAIT_MS) {
        throw new Error('This is taking far longer than usual. Please try again.');
      }
      showLoading(`Building the 3D model… (${Math.round((Date.now() - startedAt) / 1000)}s)`);

      const res = await fetch(`/api/generate-3d/status/${jobId}`);
      const data = await res.json();

      if (!res.ok || data.error) {
        throw new Error(data.error || 'Could not generate the 3D model.');
      }
      if (data.status === 'done') return data;

      await sleep(POLL_INTERVAL_MS);
    }
  }

  btnAr.addEventListener('click', async () => {
    if (!editedUrl || !lastPrompt) return;

    btnAr.disabled = true;
    showLoading('Isolating the object…');

    try {
      const isoRes = await fetch('/api/isolate-object', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_url: editedUrl, prompt: lastPrompt }),
      });
      const isoData = await isoRes.json();
      if (!isoRes.ok || isoData.error) {
        throw new Error(isoData.error || 'Could not isolate the object.');
      }

      const startRes = await fetch('/api/generate-3d/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_url: isoData.product_url }),
      });
      const startData = await startRes.json();
      if (!startRes.ok || startData.error) {
        throw new Error(startData.error || 'Could not start 3D model generation.');
      }

      const result = await pollArJob(startData.job_id, Date.now());

      arViewer.setAttribute('src', result.glb_url);
      if (result.usdz_url) arViewer.setAttribute('ios-src', result.usdz_url);

      hideLoading();
      showAr();
    } catch (err) {
      hideLoading();
      showError(err.message);
      if (editedUrl) showPhoto(editedUrl);
    } finally {
      btnAr.disabled = !editedUrl;
    }
  });
})();
