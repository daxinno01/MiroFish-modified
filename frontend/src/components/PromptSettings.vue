<template>
  <div class="settings-wrapper">
    <button class="settings-trigger" @click="toggleSettings">⚙️</button>

    <Teleport to="body">
      <Transition name="fade-slide">
        <div v-if="isOpen" class="settings-overlay" @click.self="isOpen = false">
          <div class="settings-card">
            <div class="settings-header">
              <div class="header-title-group">
                <span class="settings-icon-mini">⚙</span>
                <h3>Prompt Configurations</h3>
              </div>
              <button @click="isOpen = false" class="close-x-btn">&times;</button>
            </div>
            
            <div class="stage-tabs">
                <button 
                  v-for="stage in allStages" 
                  :key="stage"
                  :class="{ 'active': selectedStage === stage }"
                  @click="loadPrompts(stage)"
                >
                  {{ stage.replace('.txt', '').replace('_', ' ').toUpperCase() }}
                </button>
            </div>

            <div class="settings-body">
                <div 
                  v-for="(content, fileName) in prompts" 
                  :key="fileName" 
                  :ref="el => { if (el) accordionRefs[fileName] = el }"
                  class="prompt-accordion"
                >
                  
                  <div class="accordion-header" @click="togglePrompt(fileName)">
                    <label>{{ formatLabel(fileName) }}</label>
                    <span class="chevron" :class="{ 'is-open': expandedPrompt === fileName }">▼</span>
                  </div>

                  <div v-if="expandedPrompt === fileName" class="accordion-body">
                    <textarea 
                      v-model="prompts[fileName]"
                      :data-filename="fileName" 
                      @keydown.tab.prevent="handleTab"
                      @keydown.delete="handleBackspace"
                      rows="8"
                    ></textarea>
                    
                    <div class="prompt-actions">
                      <button 
                        v-if="prompts[fileName] !== originalPrompts[fileName]" 
                        class="btn-text-only" 
                        @click="handleDiscard(fileName)"
                      >
                        Discard
                      </button>
                      <button class="btn-primary" @click="handleApply(fileName)">Save Prompt</button>
                    </div>
                  </div>

                </div>
            </div>

            <div class="settings-footer"></div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onMounted, nextTick } from 'vue';
import { getStagePrompts, getPromptStages, saveStagePrompt } from '../api/settings';

const props = defineProps({
  initialStage: { type: String, required: true }
});

const isOpen = ref(false);
const allStages = ref([]);
const selectedStage = ref('');
const prompts = reactive({});
const isLoading = ref(false);

// UI State Management
const originalPrompts = reactive({}); // Snapshot cache of server state
const expandedPrompt = ref(null);    // Tracks open section
const accordionRefs = reactive({});   // Map dictionary to locate DOM components

// Toggle & Flush to Top
const togglePrompt = async (fileName) => {
  if (expandedPrompt.value === fileName) {
    expandedPrompt.value = null;
  } else {
    expandedPrompt.value = fileName;
    
    // Wait for Vue to fully open the textarea and calculate dimensions
    await nextTick();
    
    // Grab element directly out of our component dictionary
    const element = accordionRefs[fileName];
    if (element) {
      const container = element.closest('.settings-body');
      if (container) {
        // Find layout coordinates relative to parent layout container
        const targetScrollTop = element.offsetTop - 16;
        
        container.scrollTo({
          top: targetScrollTop,
          behavior: 'smooth'
        });
      }
    }
  }
};

const handleApply = async (fileName) => {
  console.log(`Saving changes for ${fileName}:`, prompts[fileName]);
  
  await saveStagePrompt({
    stage: selectedStage.value,
    filename: fileName,
    prompt: prompts[fileName]
  });
  
  // Set current content as baseline truth
  originalPrompts[fileName] = prompts[fileName];

  console.log("Done!");
};

const handleDiscard = (fileName) => {
  // Pull unchanged textual state directly out of the cache 
  prompts[fileName] = originalPrompts[fileName];
};

const handleTab = (event) => {
  event.preventDefault();
  document.execCommand('insertText', false, '\t');
};

const handleBackspace = (event) => {
  const textarea = event.target;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const fileName = textarea.getAttribute('data-filename');
  
  if (start === end && start >= 2) {
    const textBeforeCursor = textarea.value.substring(start - 2, start);
    
    if (textBeforeCursor === "  ") {
      event.preventDefault();
      textarea.value = textarea.value.substring(0, start - 2) + textarea.value.substring(start);
      prompts[fileName] = textarea.value;
      textarea.selectionStart = textarea.selectionEnd = start - 2;
    }
  }
};

const formatLabel = (fileName) => {
  return fileName
    .replace('.txt', '')
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

onMounted(async () => {
  const response = await getPromptStages();
  allStages.value = response.data;
});

const loadPrompts = async (stage) => {
  isLoading.value = true;
  selectedStage.value = stage;
  expandedPrompt.value = null; // Close current editing layouts when changing sheets
  
  try {
    const response = await getStagePrompts(stage);
    
    Object.keys(prompts).forEach(key => delete prompts[key]);
    Object.keys(originalPrompts).forEach(key => delete originalPrompts[key]);
    
    Object.assign(prompts, response.data);
    Object.assign(originalPrompts, JSON.parse(JSON.stringify(response.data))); // sever reference tree
  } catch (e) {
    console.error(e);
  } finally {
    isLoading.value = false;
  }
};

watch(() => props.initialStage, (newStage) => {
  selectedStage.value = newStage;
  loadPrompts(newStage);
});

const toggleSettings = () => {
  isOpen.value = !isOpen.value;
  if (isOpen.value && !selectedStage.value) {
    loadPrompts(props.initialStage);
  }
};
</script>

<style scoped>
/* Trigger Styling */
.settings-trigger {
  background: transparent;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  padding: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.settings-trigger:hover {
  color: #FF6B35;
  background: rgba(255, 107, 53, 0.08);
}

/* Full Screen Overlay */
.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(15, 23, 42, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 10000;
}

/* Modal Card */
.settings-card {
  background: #ffffff;
  width: 95%;
  max-width: 1000px;
  height: 90vh;
  border-radius: 16px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.settings-header {
  padding: 12px 16px;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fcfcfc;
}

.header-title-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-title-group h3 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 700;
  color: #1e293b;
}

.close-x-btn {
  background: none;
  border: none;
  font-size: 1.5rem;
  color: #94a3b8;
  cursor: pointer;
}

/* Form Container Layout */
.settings-body {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px; 
  overflow-y: auto;
  flex: 1;
  position: relative; /* 🌟 CRITICAL: Tells offsetTop to calculate from this element */
}

/* Accordion Specific Styling */
.prompt-accordion {
  border: 1px solid #e2e8f0;
  border-radius: 10px;       /* Keeps the accordion rounded */
  background: #ffffff;
  transition: all 0.2s ease;
  scroll-margin-top: 16px;
}

.accordion-header {
  padding: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  background: #fcfcfc;
  transition: background 0.2s;
  
  /* Ensures the header matches the card's rounded top corners */
  border-top-left-radius: 9px;
  border-top-right-radius: 9px;
}

.accordion-header:hover {
  background: #f1f5f9;
}

.accordion-header label {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #334155;
  cursor: pointer;
}

.chevron {
  font-size: 0.8rem;
  color: #94a3b8;
  transition: transform 0.2s ease;
}

.chevron.is-open {
  transform: rotate(180deg);
  color: #FF6B35;
}

.accordion-body {
  padding: 0 16px 16px 16px;
  border-top: 1px solid #e2e8f0;
  background: #ffffff;
}

.accordion-body textarea {
  width: 100%;
  padding: 18px;
  margin-top: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-family: 'Inter', sans-serif;
  resize: vertical;
  min-height: 480px; /* Expands the editing footprint dramatically */
  font-size: 1rem;
  line-height: 1.6;
}

.accordion-body textarea:focus {
  outline: none;
  border-color: #FF6B35;
  box-shadow: 0 0 0 3px rgba(255, 107, 53, 0.1);
}

.prompt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 12px;
}

.btn-text-only {
  background: transparent;
  border: none;
  color: #64748b;
  font-weight: 600;
  cursor: pointer;
  padding: 10px 16px;
}

.btn-text-only:hover {
  color: #ef4444;
}

/* Footer & Global Actions */
.settings-footer {
  padding: 12px 16px;
  background: #f8fafc;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  border-top: 1px solid #f1f5f9;
}

.btn-secondary {
  padding: 10px 18px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: white;
  color: #64748b;
  font-weight: 600;
  cursor: pointer;
}

.btn-primary {
  padding: 10px 18px;
  border-radius: 8px;
  border: none;
  background: #FF6B35;
  color: white;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-primary:hover { opacity: 0.9; }

/* Navigation Header Tabs */
.stage-tabs {
  display: flex;
  border-bottom: 1px solid #ddd;
  background: #f8f9fa;
}

.stage-tabs button {
  padding: 12px 20px;
  border: none;
  background: none;
  cursor: pointer;
  font-weight: 500;
  color: #666;
}

.stage-tabs button.active {
  color: #FF6B35;
  border-bottom: 2px solid #FF6B35;
}

/* Transitions */
.fade-slide-enter-active, .fade-slide-leave-active {
  transition: all 0.3s ease;
}
.fade-slide-enter-from, .fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-20px);
}
</style>