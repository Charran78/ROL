import random
import requests
import json
import gradio as gr
import sqlite3
from datetime import datetime
import logging
import pygame
import time
import re
import threading

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Inicializar pygame para sonidos
try:
    pygame.mixer.init()
    pygame_available = True
except:
    pygame_available = False
    logger.warning("Pygame no está disponible, los sonidos estarán desactivados")

# Variables globales para verificar el estado de Ollama y el modelo actual
OLLAMA_AVAILABLE = True
INSTALLED_MODELS = ["gemma2:2b-instruct-q4_K_M", "gemma2:2b", "phi3:mini", "llama3.1:8b-instruct-q4_K_M"]
CURRENT_MODEL = "gemma2:2b-instruct-q4_K_M"

def get_installed_models():
    """Obtiene la lista de modelos instalados en Ollama"""
    try:
        response = requests.get('http://localhost:11434/api/tags', timeout=5)
        if response.status_code == 200:
            models = response.json().get('models', [])
            return [model['name'] for model in models]
    except:
        pass
    return []

def check_ollama_status():
    """Verifica si Ollama está disponible y actualiza la lista de modelos"""
    global OLLAMA_AVAILABLE, INSTALLED_MODELS, CURRENT_MODEL
    try:
        response = requests.get('http://localhost:11434/api/tags', timeout=5)
        if response.status_code == 200:
            OLLAMA_AVAILABLE = True
            logger.info("✅ Ollama está ejecutándose correctamente")
            
            models = response.json().get('models', [])
            INSTALLED_MODELS = [model['name'] for model in models]
            
            if INSTALLED_MODELS:
                if CURRENT_MODEL not in INSTALLED_MODELS:
                    CURRENT_MODEL = INSTALLED_MODELS[0]
                logger.info(f"✅ Usando modelo: {CURRENT_MODEL}")
            else:
                logger.warning("❌ No se encontraron modelos en Ollama. Por favor, descarga uno.")
        else:
            logger.warning("❌ Ollama no responde correctamente")
    except Exception as e:
        logger.warning(f"❌ No se puede conectar con Ollama. La funcionalidad de IA estará limitada. Error: {e}")

# Verificar Ollama al inicio en un hilo separado
threading.Thread(target=check_ollama_status, daemon=True).start()
time.sleep(1) # Dar un pequeño tiempo para que el hilo se ejecute

# Configuración de la base de datos
def init_db():
    """Inicializa la base de datos y crea la tabla si no existe"""
    try:
        conn = sqlite3.connect('dnd_adventure.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS game_sessions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      player_action TEXT NOT NULL,
                      game_master_response TEXT NOT NULL,
                      dice_roll INTEGER,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.commit()
        # Verificar que la tabla existe
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_sessions'")
        if c.fetchone():
            logger.info("Base de datos y tabla inicializadas correctamente")
        else:
            logger.error("No se pudo verificar la creación de la tabla")
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error al inicializar la base de datos: {e}")
        raise  # Levanta el error para que sea visible

def save_game_action(player_action, gm_response, dice_roll=None):
    """Guarda una acción del juego en la base de datos"""
    if not isinstance(player_action, str) or not isinstance(gm_response, str):
        logger.error(f"Entradas inválidas: player_action={player_action}, gm_response={gm_response}")
        return
    try:
        conn = sqlite3.connect('dnd_adventure.db')
        c = conn.cursor()
        c.execute("INSERT INTO game_sessions (player_action, game_master_response, dice_roll) VALUES (?, ?, ?)",
                  (player_action, gm_response, dice_roll))
        conn.commit()
        logger.info("Acción guardada: %s", player_action[:50])
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error al guardar acción: {e}")

def tirar_dado(caras=20):
    """Simula el lanzamiento de un dado con el número de caras especificado"""
    return random.randint(1, caras)

def play_dice_sound():
    """Reproduce sonido de dado rodando"""
    if not pygame_available:
        return
        
    try:
        # Nota: El archivo 'dice-roll.mp3' debe existir en el mismo directorio.
        pygame.mixer.music.load('dice-roll.mp3')
        pygame.mixer.music.play()
    except Exception as e:
        print(f"Error reproduciendo sonido: {e}")

# Prompt mejorado para el Dungeon Master
INITIAL_PROMPT = """Eres un Dungeon Master experto de Dungeons & Dragons. 
Tu objetivo es crear una aventura épica con desafíos, NPCs interesantes y giros argumentales. 
Responde siempre en español y mantén un tono inmersivo y descriptivo.

Instrucciones específicas:
1. La aventura debe comenzar con el jugador despertando en un lugar misterioso.
2. Describe escenas de manera vívida, incluyendo detalles sensoriales (qué se ve, huele, escucha).
3. Crea NPCs con personalidades distintivas.
4. Presenta desafíos que requieran interacción del jugador.
5. Responde a las acciones del jugador de manera coherente con la historia.
6. Incorpora las tiradas de dados en la narrativa cuando sean relevantes.

¡Comienza la aventura!"""

def generar_aventura(mensaje, historial, dice_roll=None):
    """Genera una respuesta del Dungeon Master basada en la acción del jugador"""
    if not OLLAMA_AVAILABLE:
        return "❌ Error: Ollama no está disponible. Por favor, asegúrate de que Ollama esté instalado y ejecutándose."

    try:
        contexto_ollama = [{"role": "system", "content": INITIAL_PROMPT}]
        for entry in historial[-6:]:
            if isinstance(entry, tuple) and len(entry) == 2:
                user_msg, bot_msg = entry
                contexto_ollama.append({"role": "user", "content": str(user_msg) if user_msg else ""})
                if bot_msg:
                    contexto_ollama.append({"role": "assistant", "content": str(bot_msg)})

        roll_info = f"\nTirada de dado: {dice_roll}" if dice_roll else ""
        prompt = json.dumps(contexto_ollama + [{"role": "user", "content": f"{mensaje}{roll_info}"}, {"role": "assistant", "content": "Dungeon Master:"}])

        response = requests.post(
            url='http://localhost:11434/api/generate',
            headers={'Content-Type': 'application/json'},
            data=json.dumps({
                "model": CURRENT_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.9,
                    "top_p": 0.9,
                    "top_k": 40,
                    "num_predict": 512
                }
            }),
            timeout=120
        )

        if response.status_code == 200:
            respuesta = response.json()['response']
            respuesta = re.sub(r'.*Dungeon Master:', '', respuesta).strip()
            if not respuesta:  # Si la respuesta está vacía
                respuesta = "El Dungeon Master te observa en silencio, esperando una acción más clara."
            save_game_action(mensaje, respuesta, dice_roll)
            return respuesta
        else:
            logger.error(f"Error en API: {response.status_code} - {response.text}")
            return "Error: No se pudo generar una respuesta válida."
    except Exception as e:
        logger.error(f"Error de conexión: {str(e)}")
        return f"Error: El Dungeon Master está perdido en el multiverso. Intenta de nuevo."

def manejar_aventura(mensaje, historial):
    """Maneja la lógica principal de la aventura"""
    if not mensaje.strip():
        return historial, "", historial  # No hacer nada si el mensaje está vacío

    dice_roll = None
    player_message = mensaje
    if "tirar d" in mensaje.lower():
        try:
            match = re.search(r'tirar d(\d+)', mensaje.lower())
            if match:
                caras = int(match.group(1))
                dice_roll = tirar_dado(caras)
                play_dice_sound()
                player_message += f" (Resultado: {dice_roll})"
        except Exception as e:
            logger.error(f"Error al procesar tirada de dado: {e}")

    # Generar respuesta del Dungeon Master
    gm_response = generar_aventura(mensaje, historial, dice_roll)
    
    # Validar que gm_response sea un string válido
    if not isinstance(gm_response, str) or not gm_response.strip():
        gm_response = "El Dungeon Master murmura algo ininteligible... ¿Puedes repetir tu acción?"
    
    # Asegurarse de que player_message sea string
    player_message = str(player_message)
    
    # Añadir al historial
    historial.append((player_message, gm_response))
    
    return historial, "", historial

def load_game_history():
    """Carga el historial de sesiones desde la base de datos"""
    try:
        conn = sqlite3.connect('dnd_adventure.db')
        c = conn.cursor()
        c.execute("SELECT player_action, game_master_response FROM game_sessions ORDER BY timestamp")
        history = [(row[0], row[1]) for row in c.fetchall()]
        conn.close()
        logger.info("Historial cargado con %d entradas", len(history))
        return history
    except sqlite3.Error as e:
        logger.error(f"Error al cargar historial: {e}")
        return []

# CSS estilo D&D
dnd_css = """
.gradio-container {
    background: url('https://i.postimg.cc/yN2F8hxH/goldenaxe.png');
    background-size: cover;
    font-family: 'Times New Roman', serif;
}
.chatbot {
    background-color: rgba(249, 245, 235, 0.9);
    border: 2px solid #8B4513;
    border-radius: 10px;
}
.textbox {
    background-color: rgba(249, 245, 235, 0.9);
    border: 2px solid #8B4513;
}
.button {
    background: linear-gradient(180deg, #8B4513 0%, #6B3100 100%);
    color: #F9F5EB;
    border: 1px solid #4A2C1D;
    border-radius: 5px;
}
.button:hover {
    background: linear-gradient(180deg, #6B3100 0%, #4A2C1D 100%);
}
h1 {
    color: #8B4513;
    text-shadow: 2px 2px 4px #000000;
    font-family: 'Times New Roman', serif;
}
.status-warning {
    color: #ff9900;
    font-weight: bold;
    background-color: #fff4e6;
    padding: 10px;
    border-radius: 5px;
    border: 1px solid #ff9900;
}
.model-selector {
    background-color: rgba(139, 69, 19, 0.8);
    padding: 10px;
    border-radius: 5px;
    margin-bottom: 10px;
}
"""

# Inicializar la base de datos
init_db()

# Crear la interfaz de aventura D&D
with gr.Blocks(theme=gr.themes.Soft(), css=dnd_css, title="🐉 Aventura D&D con IA") as demo:
    gr.Markdown("# 🐉 Aventura de Dungeons & Dragons")
    
    gr.Markdown("¡Embárcate en una aventura épica con un Dungeon Master impulsado por IA!")
    
    # Selector de modelo
    with gr.Row():
        model_selector = gr.Dropdown(
            choices=INSTALLED_MODELS,
            value=CURRENT_MODEL,
            label="Selecciona el modelo de IA",
            interactive=OLLAMA_AVAILABLE
        )
        
        def update_model(model_name):
            global CURRENT_MODEL
            CURRENT_MODEL = model_name
            return model_name
        
        model_selector.change(update_model, model_selector, model_selector)
    
    # Estado para almacenar el historial de la conversación
    historial_state = gr.State([])
    
    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                label="Aventura",
                height=500,
                show_label=False
            )
            
            with gr.Row():
                mensaje = gr.Textbox(
                    label="Tu acción",
                    placeholder="Describe tu acción (ej: 'Ataco al orco con mi espada' o 'Tirar d20 para investigar')...",
                    lines=2
                )
                
            with gr.Row():
                enviar = gr.Button("🎯 Realizar acción", variant="primary")
                limpiar = gr.Button("🔄 Nueva aventura")
                
        with gr.Column(scale=1):            
            gr.Markdown("## 📜 Historial de Aventuras")
            history_table = gr.Dataframe(headers=["Acción", "Respuesta", "Dado", "Fecha"])
            gr.Button("Cargar Historial").click(
                fn=lambda: [(r[0], r[1], r[2], r[3]) for r in sqlite3.connect('dnd_adventure.db').execute("SELECT player_action, game_master_response, dice_roll, timestamp FROM game_sessions").fetchall()],
                outputs=history_table
            )

            gr.Markdown("## 🎲 Dados de RPG")
            with gr.Row():
                gr.Button("d4", variant="secondary").click(
                    fn=lambda: "Tirar d4", outputs=mensaje
                )
                gr.Button("d6", variant="secondary").click(
                    fn=lambda: "Tirar d6", outputs=mensaje
                )
                gr.Button("d8", variant="secondary").click(
                    fn=lambda: "Tirar d8", outputs=mensaje
                )
            
            with gr.Row():
                gr.Button("d10", variant="secondary").click(
                    fn=lambda: "Tirar d10", outputs=mensaje
                )
                gr.Button("d12", variant="secondary").click(
                    fn=lambda: "Tirar d12", outputs=mensaje
                )
                gr.Button("d20", variant="secondary").click(
                    fn=lambda: "Tirar d20", outputs=mensaje
                )
            
            gr.Markdown("## 📜 Acciones rápidas")
            gr.Button("🔍 Investigar área", variant="secondary").click(
                fn=lambda: "Investigo el área cuidadosamente", outputs=mensaje
            )
            gr.Button("🗣️ Hablar con NPC", variant="secondary").click(
                fn=lambda: "Intento conversar con el personaje", outputs=mensaje
            )
            gr.Button("⚔️ Atacar", variant="secondary").click(
                fn=lambda: "Ataco al enemigo con mi arma", outputs=mensaje
            )
    
    def reset_and_start_adventure():
        """Función para limpiar el historial y comenzar una nueva aventura"""
        initial_message = "Te despiertas en una taberna oscura. El olor a cerveza rancia y madera vieja llena tus fosas nasales. Una figura encapuchada se acerca a tu mesa..."
        # Usar una cadena vacía para el mensaje del usuario en lugar de None
        initial_chat_history = [("", initial_message)]
        initial_state_history = initial_chat_history
        return initial_chat_history, "", initial_state_history

    # Configurar eventos
    mensaje.submit(
        fn=manejar_aventura,
        inputs=[mensaje, historial_state],
        outputs=[chatbot, mensaje, historial_state]
    )
    
    enviar.click(
        fn=manejar_aventura,
        inputs=[mensaje, historial_state],
        outputs=[chatbot, mensaje, historial_state]
    )
    
    limpiar.click(
        fn=reset_and_start_adventure,
        inputs=None,
        outputs=[chatbot, mensaje, historial_state]
    )
    
    # Inicializar la aventura al cargar la página
    demo.load(
    fn=lambda: (load_game_history() or [("", "Te despiertas en una taberna oscura. El olor a cerveza rancia y madera vieja llena tus fosas nasales. Una figura encapuchada se acerca a tu mesa...")], "", load_game_history()),
    inputs=None,
    outputs=[chatbot, mensaje, historial_state]
)

# Ejecutar la aplicación
if __name__ == "__main__":
    demo.launch(
        share=False,
        server_name="0.0.0.0",
        server_port=7861,
        show_error=True,
        pwa=True
    )
