# KnowledgeOS
## Contextual Memory Operating System for AI Agents
### Architecture Specification v0.2

---

# Abstract

KnowledgeOS es una capa de memoria persistente y contextual para agentes de inteligencia artificial.

Su objetivo no es almacenar conversaciones, sino construir una representación estructurada del conocimiento del usuario, sus proyectos, entidades, decisiones y experiencias.

KnowledgeOS permite que diferentes agentes de IA compartan una misma memoria independiente del modelo utilizado.

Compatible con:

- Claude
- ChatGPT
- Gemini
- Agentes personalizados
- Sistemas MCP
- Automatizaciones

La premisa principal:

> El conocimiento pertenece al usuario, no al modelo.

---

# Problema

Actualmente los asistentes de IA tienen memoria limitada y fragmentada.

El conocimiento termina distribuido entre:

- MEMORY.md
- CLAUDE.md
- AGENTS.md
- Notion
- Obsidian
- GitHub
- Wikis
- Documentación interna
- Conversaciones anteriores

Problemas:

- pérdida de contexto;
- duplicación;
- mezcla de conceptos;
- falta de continuidad;
- dependencia del proveedor.

---

# Visión

Crear una capa universal de conocimiento donde cualquier agente pueda consultar:

- quién es el usuario;
- qué proyectos existen;
- qué decisiones se tomaron;
- qué procedimientos existen;
- qué información pertenece a cada contexto.

KnowledgeOS debe comportarse como una memoria humana:

- recordar;
- relacionar;
- olvidar;
- priorizar;
- distinguir contextos.

---

# Principios de diseño

## Model Agnostic

La memoria no pertenece a Claude, GPT o Gemini.

Los modelos son clientes.

---

## Self Hosted

El usuario controla:

- datos;
- infraestructura;
- seguridad;
- almacenamiento.

---

## Context First

La recuperación no depende únicamente de similitud semántica.

Debe entender:

- quién;
- qué;
- cuándo;
- dónde;
- en qué contexto.

---

## Memory over Conversation

No se almacenan chats completos.

Se almacenan conocimientos.

---

# Arquitectura General

```
                         Usuario

                            |

                            v

                     AI Assistant

                  Claude / GPT / Gemini

                            |

                            v

                          MCP

                            |

                            v

                 Context & Memory Gateway


        +-------------------+-------------------+

        |                                       |

        v                                       v


 Context Engine                       Memory Engine

        |                                       |

        |                                       |

        v                                       v


Entity Resolution                  Retrieval System

Intent Detection                   Vector Search

Ambiguity Resolution               Knowledge Graph


        |                                       |

        +-------------------+-------------------+

                            |

                            v


                    Knowledge Storage


             PostgreSQL + Vector DB + Graph


```

---

# Componentes

# 1. MCP Server

Es la interfaz entre los agentes y KnowledgeOS.

Expone herramientas:

```
memory.search()

memory.remember()

memory.update()

memory.forget()

memory.related()

memory.timeline()

memory.recent()

memory.projects()
```

---

# 2. Context Engine

Es la capa que permite que la memoria sea inteligente.

Su función:

- detectar contexto actual;
- resolver ambigüedades;
- identificar entidades;
- evitar contaminación entre dominios.

No responde al usuario.

Solo ayuda al sistema a decidir qué conocimiento utilizar.

---

# Ejemplo de ambigüedad

Memoria A:

```
Proyecto:
Expense Tracker

Tipo:
Software

Información:

La aplicación calcula gastos mensuales.
```

---

Memoria B:

```
Área:
Finanzas personales

Información:

Mis gastos mensuales son 1200 USD.
```

---

Pregunta:

```
¿Cuánto gasté este mes?
```

Un buscador semántico puede confundirse.

Context Engine:

```
Intención:
Finanzas personales

Entidad:
Usuario

Excluir:
Expense Tracker
```

Resultado:

Consulta únicamente:

```
Finanzas personales
```

---

# 3. Modelo auxiliar local

KnowledgeOS no debe usar siempre el modelo principal para resolver contexto.

Debe existir una capa ligera.

Responsabilidades:

- clasificación;
- extracción;
- resolución de entidades;
- detección de ambigüedad.

---

## Modelos posibles

Ejemplos:

- Qwen 2.5 1.5B / 3B
- Llama 3.2 1B / 3B
- Phi Mini
- MiniLM
- DistilBERT

Ejecutados localmente mediante:

- Ollama
- llama.cpp
- vLLM

---

# Estrategia de activación

El modelo auxiliar no se ejecuta siempre.

Flujo:

```
Pregunta

 |

v

Fast Router

 |

¿Existe ambigüedad?

 |

+--------------+

|              |

No             Sí

|              |

v              v

Buscar       Modelo pequeño

directo      resolver contexto

```

---

# Latencia objetivo

KnowledgeOS debe priorizar velocidad.

Objetivos:

| Operación | Latencia |
|-|-:|
| Router simple | <5ms |
| Embedding consulta | 20-100ms |
| Vector Search | 5-20ms |
| PostgreSQL | 5-20ms |
| Modelo auxiliar | 100-500ms |

La mayoría de consultas deben evitar ejecutar modelos auxiliares.

---

# Memory Engine

Gestiona la memoria persistente.

Tipos de memoria:

## Semantic Memory

Conocimiento estable.

Ejemplo:

```
El proyecto Billing utiliza PostgreSQL.
```

---

## Episodic Memory

Eventos.

Ejemplo:

```
El 5 de agosto se migró Redis a Valkey.
```

---

## Procedural Memory

Procesos.

Ejemplo:

```
Antes de desplegar ejecutar migraciones.
```

---

## Decision Memory

Decisiones tomadas.

Ejemplo:

```
Se decidió utilizar Docker Compose en producción.
```

---

# Modelo de datos

Una memoria contiene:

```
ID

Título

Contenido

Tipo

Entidades relacionadas

Contexto

Proyecto

Fecha creación

Última actualización

Importancia

Confianza

Fuente

Versiones

Embedding
```

---

Ejemplo:

```json
{
"title":"Producción usa Ubuntu 24",

"type":"Infrastructure",

"entities":[
 "VPS Production"
],

"confidence":0.98,

"importance":0.9
}
```

---

# Knowledge Graph

La memoria debe entender relaciones.

Ejemplo:

```
Usuario

 |
 desarrolla

 |

Expense Tracker

 |
 usa

 |

React

 |
 desplegado en

 |

VPS
```

---

El grafo permite:

- evitar mezclas;
- navegar conocimiento;
- encontrar relaciones ocultas.

---

# Retrieval System

No utiliza solamente búsqueda vectorial.

Utiliza búsqueda híbrida.

```
Consulta

 |

Embedding

 |

Vector Search

+

Keyword Search

+

Graph Traversal

+

Metadata Filtering

 |

Reranking

 |

Contexto final

```

---

# Storage

## PostgreSQL

Fuente principal.

Guarda:

- memorias;
- relaciones;
- usuarios;
- permisos;
- historial.

---

## Vector Database

Opciones:

- Qdrant
- pgvector
- Weaviate

Guarda:

- embeddings;
- índices semánticos.

---

## Redis

Uso:

- cache;
- sesiones;
- consultas frecuentes.

---

# Seguridad

## Credenciales

Nunca almacenar secretos.

Incorrecto:

```
AWS_SECRET=xxxx
```

Correcto:

```
Vault reference:

secret://production/aws
```

---

# Context Isolation

Cada memoria debe tener identidad.

Ejemplo:

```
Expense Tracker

=

Proyecto software
```

No:

```
Finanzas personales
```

Aunque compartan palabras.

---

# Aprendizaje continuo

KnowledgeOS puede mejorar con el uso.

Ejemplo:

Sistema:

```
¿Hablas de Expense Tracker o finanzas personales?
```

Usuario:

```
Finanzas personales
```

Se aprende:

```
Cuando usuario menciona gastos
en contexto personal:

Priorizar Finanzas personales.
```

---

# Deployment recomendado

Para uso personal:

```
VPS

4 CPU

8GB RAM

100GB SSD
```

Servicios:

```
Docker Compose

|

+ PostgreSQL

+ Qdrant

+ Redis

+ API

+ MCP Server

+ Ollama

```

---

# Flujo completo

```
Usuario

 |

Pregunta

 |

Context Engine

 |

Detecta:

- intención
- entidades
- contexto

 |

Memory Retrieval

 |

Knowledge Graph

 |

Vector Search

 |

Resultados relevantes

 |

Claude/GPT

 |

Respuesta

```

---

# Roadmap

## v0.1

Base:

- MCP
- API
- PostgreSQL
- Vector Search
- remember/search

---

## v0.2

Context Engine:

- entidades
- proyectos
- resolución ambigua
- clasificación

---

## v0.3

Knowledge Graph:

- relaciones
- navegación
- contexto avanzado

---

## v0.4

Connectors:

- GitHub
- Notion
- Obsidian
- Google Drive
- Calendar

---

## v1.0

Sistema completo:

- memoria persistente;
- contexto inteligente;
- multiagente;
- independiente del modelo.

---

# Visión final

KnowledgeOS no pretende ser otro sistema RAG.

Un RAG responde:

> "¿Qué documentos son similares?"

KnowledgeOS responde:

> "¿Qué conocimiento de mi vida, proyectos y experiencias es relevante para esta situación concreta?"

La diferencia es la comprensión del contexto.

El objetivo final:

Crear una memoria universal, privada y autoalojable que permita a cualquier agente de IA trabajar con continuidad, identidad y conocimiento acumulado.

---
## Posibles modelos auxiliares.

### DistilBERT (Mini modelo local)

```python
#!/usr/bin/env python3
import argparse
from transformers import pipeline

def main():
    parser = argparse.ArgumentParser(description="Run DistilBERT via CLI")
    parser.add_argument("text", type=str, help="The input text to process")
    parser.add_argument(
        "--task", 
        type=str, 
        default="sentiment-analysis", 
        help="Hugging Face pipeline task (default: sentiment-analysis)"
    )
    parser.add_argument(
        "--model", 
        type=str, 
        default="distilbert-base-uncased-finetuned-sst-2-english", 
        help="Specific model checkpoint"
    )
    
    args = parser.parse_args()
    
    # Initialize the NLP pipeline
    classifier = pipeline(args.task, model=args.model)
    
    # Run inference
    result = classifier(args.text)
    print(result)

if __name__ == "__main__":
    main()
```
### Otra forma:

```python
from transformers import pipeline

classifier = pipeline(
    "zero-shot-classification",
    model="typeform/distilbert-base-uncased-mnli"
)

texto = "Quiero que hagas un reporte de mis finanzas"

categorias = [
    "finanzas_personales",
    "app_gestion_financiera"
]

resultado = classifier(
    texto,
    candidate_labels=categorias
)

print(resultado)
```