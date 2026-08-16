"""Finite QCC NiceLabel catalog derived from the approved template filenames."""

from __future__ import annotations

from typing import Any


def _template(
    filename: str,
    brand: str,
    strain: str,
    sku_type: str,
    operation: str,
    confidence: str = "Confirmed",
    note: str = "",
) -> dict[str, Any]:
    return {
        "Template Name": filename.removesuffix(".nlbl"),
        "Template File": filename,
        "Brand": brand,
        "Strain": strain,
        "SKU Type": sku_type,
        "Operation": operation,
        "Confidence": confidence,
        "Review Note": note,
    }


NICE_LABEL_CATALOG: list[dict[str, Any]] = [
    _template("Banana Meltshake Piatella 1g.nlbl", "Brand Needs Review", "Banana Meltshake", "1g Piatella", "Manufacturing", "Needs Review", "Confirm the finished-product brand."),
    _template("Blue Dream 3.5g.nlbl", "Clade9", "Blue Dream", "3.5g Flower", "Cultivation"),
    _template("Blue Dream 7g.nlbl", "Clade9", "Blue Dream", "7g Flower", "Cultivation"),
    _template("Candy Cut IWH 1g Infused Pre Roll.nlbl", "Craft Kings", "Candy Cut", "1g IWH Infused Pre-Roll", "Manufacturing"),
    _template("Chem Haze 5pk IWH Infused Pre Rolls 3.5g.nlbl", "Craft Kings", "Chem Haze", "3.5g IWH Infused Pre-Rolls 5-Pack", "Manufacturing"),
    _template("Diamond Bar 1g Pre Roll.nlbl", "Clade9", "Diamond Bar", "1g Pre-Roll", "Cultivation"),
    _template("Diamond Bar Live Rosin 1g.nlbl", "Clade9", "Diamond Bar", "1g Live Rosin", "Manufacturing"),
    _template("Diamond Bar Vape LR 0.5g.nlbl", "Clade9", "Diamond Bar", "0.5g Vape LR", "Manufacturing"),
    _template("Gelato Bomb Live Rosin 1g.nlbl", "Brand Needs Review", "Gelato Bomb", "1g Live Rosin", "Manufacturing", "Needs Review", "Confirm whether this is Clade9 or another finished-product brand."),
    _template("Golden Goat 14g Flower.nlbl", "Craft Kings", "Golden Goat", "14g Flower", "Cultivation"),
    _template("Golden Goat 28g Flower.nlbl", "Craft Kings", "Golden Goat", "28g Flower", "Cultivation"),
    _template("Golden Goat 3.5g Flower.nlbl", "Craft Kings", "Golden Goat", "3.5g Flower", "Cultivation"),
    _template("Gummies Blue Raspberry.nlbl", "Craft Kings", "Blue Raspberry Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Green Apple.nlbl", "Craft Kings", "Green Apple Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Peach.nlbl", "Craft Kings", "Peach Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Pineapple.nlbl", "Craft Kings", "Pineapple Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Pink Lemonade.nlbl", "Craft Kings", "Pink Lemonade Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Strawberry.nlbl", "Craft Kings", "Strawberry Gummies", "Gummies", "Manufacturing"),
    _template("Gummies Watermelon.nlbl", "Craft Kings", "Watermelon Gummies", "Gummies", "Manufacturing"),
    _template("Hybrid Blend 1g Infused Pre Roll.nlbl", "Craft Kings", "Hybrid Blend", "1g Infused Pre-Roll", "Manufacturing"),
    _template("Hybrid Blend 1g Pre Roll.nlbl", "Craft Kings", "Hybrid Blend", "1g Pre-Roll", "Cultivation"),
    _template("Hybrid Blend 5pk Infused Pre Rolls 3.5g.nlbl", "Craft Kings", "Hybrid Blend", "3.5g Infused Pre-Rolls 5-Pack", "Manufacturing"),
    _template("J1 5pk Pre Rolls 3.5g.nlbl", "Clade9", "J1", "3.5g Pre-Rolls 5-Pack", "Cultivation"),
    _template("Orange Fig Bar Wet Badder.nlbl", "Locals Only", "Orange Fig Bar", "Wet Badder 1g", "Manufacturing", "Likely Match", "Confirm the printed net weight on the NiceLabel design."),
    _template("Orange Fig Bar Wet Diamonds.nlbl", "Locals Only", "Orange Fig Bar", "Wet Diamonds 1g", "Manufacturing", "Likely Match", "Confirm the printed net weight on the NiceLabel design."),
    _template("Tahoe OG Cured Resin.nlbl", "Clade9", "Tahoe OG", "Cured Resin - Needs Review", "Manufacturing", "Needs Review", "Confirm vape versus concentrate and the printed net weight."),
    _template("The Velvet Elvis 1g.nlbl", "Brand Needs Review", "The Velvet Elvis", "1g Product - Needs Review", "Manufacturing", "Needs Review", "Confirm the brand and finished SKU type."),
    _template("Vigor Vanish 1g Pre Roll.nlbl", "Brand Needs Review", "Vigor Vanish", "1g Pre-Roll", "Cultivation", "Needs Review", "Confirm the finished-product brand."),
    _template("Vigor Vibe 1g Pre Roll.nlbl", "Brand Needs Review", "Vigor Vibe", "1g Pre-Roll", "Cultivation", "Needs Review", "Confirm the finished-product brand."),
    _template("Vigor Vitalize 1g Pre Roll.nlbl", "Brand Needs Review", "Vigor Vitalize", "1g Pre-Roll", "Cultivation", "Needs Review", "Confirm the finished-product brand."),
]

