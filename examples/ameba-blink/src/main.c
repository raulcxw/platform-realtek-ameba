/*
 * src/main.c — PIO project's user code entry point.
 *
 * Demonstrates v0.3 EXTERN_DIR mode capabilities:
 *   1. User code lives in PIO-standard src/ directory.
 *   2. Direct access to Ameba SDK headers (ameba_soc.h, etc.).
 *   3. FreeRTOS APIs available out of the box.
 *   4. Multi-file user projects work (see also: src/blink_helpers.c).
 *
 * Hooked into the SDK via app_example/app_main.c which calls user_main().
 */

#include "ameba_soc.h"
#include "FreeRTOS.h"
#include "task.h"

/* Forward declaration -- implemented in src/blink_helpers.c */
extern void log_blink_message(int count);

/*
 * GPIO pin to toggle. RTL8721F dev board commonly exposes _PA_15 on a
 * silkscreen-labeled pin. Change here for your board.
 */
#define BLINK_GPIO  _PA_15

static void blink_task(void *param)
{
    (void)param;
    GPIO_InitTypeDef gpio_init = {0};

    gpio_init.GPIO_Pin = BLINK_GPIO;
    gpio_init.GPIO_Mode = GPIO_Mode_OUT;
    gpio_init.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(&gpio_init);

    int count = 0;
    while (1) {
        GPIO_WriteBit(BLINK_GPIO, 1);
        vTaskDelay(pdMS_TO_TICKS(500));
        GPIO_WriteBit(BLINK_GPIO, 0);
        vTaskDelay(pdMS_TO_TICKS(500));

        log_blink_message(++count);
    }
}

void user_main(void)
{
    DiagPrintf("[blink] hello from src/main.c! starting blink_task on %s\n",
               "RTL8721F");

    if (xTaskCreate(blink_task, "blink", 256, NULL,
                    tskIDLE_PRIORITY + 1, NULL) != pdPASS) {
        DiagPrintf("[blink] FATAL: xTaskCreate failed\n");
    }
}
