
document.addEventListener('DOMContentLoaded', function() {
  // Éléments du DOM
  const form = document.getElementById('modifyAdForm');
  const descriptionTextarea = document.getElementById('offre');
  const charCount = document.getElementById('charCount');
  const charCounterBar = document.getElementById('charCounterBar');
  const submitBtn = document.getElementById('submitBtn');

  // Modal
  const modal = document.getElementById("rulesModal");
  const openModalBtn = document.getElementById("openModal");
  const closeModalBtn = document.getElementById("closeModalBtn");

  // Compteur de caractères
  function updateCharCount() {
    const currentLength = descriptionTextarea.value.replace(/\s/g, '').length;
    charCount.textContent = `${currentLength} / 200`;
    
    const percentage = Math.min((currentLength / 200) * 100, 100);
    charCounterBar.style.width = `${percentage}%`;
    
    if (currentLength < 200) {
      charCounterBar.style.backgroundColor = '#ef4444'; // Rouge
      submitBtn.disabled = true;
      submitBtn.classList.add('opacity-50');
    } else {
      charCounterBar.style.backgroundColor = '#22c55e'; // Vert
      submitBtn.disabled = false;
      submitBtn.classList.remove('opacity-50');
    }
  }

  // Initialiser le compteur
  updateCharCount();
  
  // Événement sur la saisie
  descriptionTextarea.addEventListener('input', updateCharCount);

  // Gestion de la modal
  openModalBtn.onclick = function(e) {
    e.preventDefault();
    modal.classList.remove('hidden');
  }

  closeModalBtn.onclick = function() {
    modal.classList.add('hidden');
  }

  window.onclick = function(event) {
    if (event.target == modal) {
      modal.classList.add('hidden');
    }
  }

  // Validation du formulaire
  form.addEventListener('submit', function(e) {
    const description = descriptionTextarea.value;
    const codeOrLink = document.getElementById('code_ou_lien').value;
    
    // Vérifier que la description a au moins 200 caractères (sans espaces)
    if (description.replace(/\s/g, '').length < 200) {
      e.preventDefault();
      alert('La description doit contenir au moins 200 caractères (lettres).');
      return false;
    }
    
    // Vérifier le code/lien
    if (!codeOrLink.trim()) {
      e.preventDefault();
      alert('Veuillez entrer un code de parrainage ou un lien.');
      return false;
    }
    
    // Confirmation avant modification
    if (!confirm('Êtes-vous sûr de vouloir modifier cette annonce ?')) {
      e.preventDefault();
      return false;
    }
    
    return true;
  });
});
